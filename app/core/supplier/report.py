import csv
from datetime import timezone
import re
from typing import Dict, Tuple, List
from fastapi import HTTPException
import urllib
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment
from fastapi.responses import StreamingResponse
from app.core.utils.db_utils import *
import io
from app.core.config import get_settings  # Import settings
from azure.storage.blob import BlobServiceClient
import zipfile
from app.schemas.logger import logger
import io, zipfile
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from botocore.exceptions import ClientError

async def report_bulk_download(session_id: str):
    try:
        print("bulk check 1")
        storage_url = get_settings().storage.storage_account_url
        container_name = get_settings().storage.container_name
        sas_token = str(get_settings().storage.sas_token)
        print("bulk check 2")

        blob_service_client = BlobServiceClient(
            account_url=storage_url,
            credential=sas_token
        )
        container_client = blob_service_client.get_container_client(container_name)

        logger.info(f"[BULK DEBUG] container_name = {container_name}")
        logger.info(f"[BULK DEBUG] session_id = {session_id}")

        session_prefix = f"{session_id}/"
        logger.info(f"[BULK DEBUG] session_prefix = {session_prefix}")

        blob_list = list(container_client.list_blobs(name_starts_with=session_prefix))

        logger.info(f"[BULK DEBUG] blob count = {len(blob_list)}")
        for blob in blob_list:
            logger.info(f"[BULK DEBUG] blob found = {blob.name}")

        if not blob_list:
            raise HTTPException(
                status_code=404,
                detail=f"No files found for session_id {session_id}"
            )

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for blob in blob_list:
                blob_name = blob.name

                if blob_name.endswith("/"):
                    logger.info(f"[BULK DEBUG] skipping folder blob = {blob_name}")
                    continue

                logger.info(f"[BULK DEBUG] downloading blob = {blob_name}")

                blob_client = blob_service_client.get_blob_client(
                    container=container_name,
                    blob=blob_name
                )
                file_data = blob_client.download_blob().readall()

                logger.info(f"[BULK DEBUG] downloaded bytes = {len(file_data)} for blob = {blob_name}")
                logger.info(f"[BULK DEBUG] first 8 bytes = {file_data[:8]}")

                relative_path = blob_name[len(session_prefix):]
                logger.info(f"[BULK DEBUG] writing zip entry = {relative_path}")

                zip_file.writestr(relative_path, file_data)

        zip_buffer.seek(0)
        zip_bytes = zip_buffer.getvalue()

        logger.info(f"[BULK DEBUG] final zip size = {len(zip_bytes)}")

        return zip_bytes, f"{session_id}.zip"

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in report_bulk_download: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to bulk download reports: {str(e)}")

async def report_download(session_id: str, ens_id: str, type_of_file: str):
    try:
        storage_url = get_settings().storage.storage_account_url
        container_name = get_settings().storage.container_name
        sas_token = str(get_settings().storage.sas_token)

        blob_service_client = BlobServiceClient(
            account_url=storage_url,
            credential=sas_token
        )
        container_client = blob_service_client.get_container_client(container_name)

        logger.info(f"[DEBUG] container_name = {container_name}")
        logger.info(f"[DEBUG] session_id = {session_id}")
        logger.info(f"[DEBUG] ens_id = {ens_id}")
        logger.info(f"[DEBUG] type_of_file = {type_of_file}")

        # Same approach as report_bulk_download: list the actual blobs under
        # this ens_id's prefix instead of constructing the exact blob name
        # ourselves. The report's stored filename includes the entity name,
        # which nothing upstream (the endpoint, the frontend) actually
        # knows or sends — trying to build that path from a `file_name`
        # argument that's never supplied was the original bug here (a
        # TypeError on every call, silently returned as a 200 OK JSON body
        # by the endpoint's broad except handler, which the frontend then
        # saved as a corrupt .docx).
        ens_prefix = f"{session_id}/{ens_id}/"
        ens_blobs = list(container_client.list_blobs(name_starts_with=ens_prefix))
        for blob in ens_blobs:
            logger.info(f"[DEBUG][ENS] {blob.name}")

        matching_blobs = [
            blob for blob in ens_blobs
            if not blob.name.endswith('/') and blob.name.lower().endswith(f".{type_of_file.lower()}")
        ]

        if not matching_blobs:
            raise HTTPException(
                status_code=404,
                detail=f"No {type_of_file} report found for session_id={session_id}, ens_id={ens_id}",
            )

        # If more than one matches (shouldn't normally happen), take the
        # most recently modified one rather than an arbitrary listing order.
        target_blob = max(matching_blobs, key=lambda b: b.last_modified)
        blob_name = target_blob.name
        logger.info(f"[DEBUG] resolved blob_name = {blob_name}")

        blob_client = blob_service_client.get_blob_client(
            container=container_name,
            blob=blob_name
        )

        stream = blob_client.download_blob()
        file_data = stream.readall()

        logger.info(f"[DEBUG] Downloaded bytes = {len(file_data)}")
        logger.info(f"[DEBUG] First 8 bytes = {file_data[:8]}")

        # Return the actual stored filename (minus the session/ens prefix),
        # not a client-supplied one.
        result_filename = blob_name[len(ens_prefix):]
        return file_data, result_filename

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in report_download: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to download report: {str(e)}")


import os
import boto3
from botocore.exceptions import ClientError

# Initialize R2 S3 client
def get_r2_client():
    return boto3.client(
        "s3",
        aws_access_key_id=  get_settings().r2_storage.access_key, 
        aws_secret_access_key= get_settings().r2_storage.secreate_account_key, 
        endpoint_url= get_settings().r2_storage.storage_account_url,
        region_name="auto",
        verify=False
    )


async def r2_report_bulk_download(session_id: str) -> Dict:
    try:
        bucket_name = get_settings().r2_storage.storage_container_name
        s3 = get_r2_client()

        # Prefix points to session-specific folder
        prefix = f"{session_id}/"
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)

        if "Contents" not in response or not response["Contents"]:
            raise HTTPException(status_code=404, detail=f"No files found for session_id {session_id}")

        blob_list = response["Contents"]

        # Prepare in-memory zip
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for obj in blob_list:
                key = obj["Key"]
                if key.endswith("/"):  # Skip folders
                    continue
                file_obj = s3.get_object(Bucket=bucket_name, Key=key)
                file_data = file_obj["Body"].read()

                # Add to zip with path after session_id
                relative_path = key.split(f"{bucket_name}/{session_id}/", 1)[-1]
                zip_file.writestr(relative_path, file_data)

        zip_buffer.seek(0)
        return zip_buffer.getvalue(), f"{session_id}.zip"

    except ClientError as e:
        logger.error(f"Error in report_bulk_download (R2): {str(e)}")
        return {"error": str(e)}


async def r2_report_download(session_id: str, ens_id: str, type_of_file: str) -> Dict:
    try:
        bucket_name = get_settings().r2_storage.storage_container_name
        s3 = get_r2_client()

        # Folder prefix inside R2
        folder_path = f"{session_id}/{ens_id}/"

        # List all objects in the specified prefix
        response = s3.list_objects_v2(Bucket=bucket_name, Prefix=folder_path)

        if "Contents" not in response:
            raise HTTPException(status_code=404, detail=f"No files found in R2 for {ens_id}")

        # Filter files that match type_of_file
        matching_files = [
            obj for obj in response["Contents"]
            if obj["Key"].endswith(f".{type_of_file}")
        ]

        if not matching_files:
            raise HTTPException(status_code=404, detail=f"No matching {type_of_file} file found for {ens_id}")

        # Get the latest file (by LastModified)
        latest_file = max(matching_files, key=lambda x: x["LastModified"])
        latest_file_key = latest_file["Key"]

        decoded_filename = urllib.parse.unquote(os.path.basename(latest_file_key))

        # Download the file data
        file_obj = s3.get_object(Bucket=bucket_name, Key=latest_file_key)
        file_data = file_obj["Body"].read()

        return file_data, decoded_filename

    except ClientError as e:
        logger.error(f"Error in report_download (R2): {str(e)}")
        return {"error": str(e)}


async def r2_screener_report_bulk_download(session_ids: List[str]) -> Tuple[bytes, str]:
    try:
        bucket_name = get_settings().r2_storage.storage_container_name
        s3 = get_r2_client()

        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for session_id in session_ids:
                prefix = f"{session_id}/"
                response = s3.list_objects_v2(Bucket=bucket_name, Prefix=prefix)

                if "Contents" not in response or not response["Contents"]:
                    logger.warning(f"No files found for session_id {session_id}")
                    continue

                for obj in response["Contents"]:
                    key = obj["Key"]

                    # Skip folders or non-ens_id content (e.g., files directly under session folder)
                    if key.endswith("/") or key.count("/") < 2:
                        continue

                    # Extract ens_id from key structure like: session_id/ens_id/file.ext
                    relative_key = key[len(prefix):]
                    ens_id = relative_key.split("/", 1)[0]

                    if not ens_id:  # Just in case
                        continue

                    file_obj = s3.get_object(Bucket=bucket_name, Key=key)
                    file_data = file_obj["Body"].read()

                    # Add file using ens_id folder only (flatten session level)
                    zip_path = f"{ens_id}/{relative_key.split('/', 1)[1]}"
                    zip_file.writestr(zip_path, file_data)
                    
        zip_buffer.seek(0)
        return zip_buffer.getvalue(), "all-reports.zip"

    except ClientError as e:
        logger.error(f"Error in report_bulk_download (R2): {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to download reports")
    
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

def _parse_date_only_utc(d: str) -> datetime:
    if not DATE_RE.match(d):
        raise ValueError("Date must be in YYYY-MM-DD format")
    return datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)

async def download_notification_csv_(
    *, session, start_date: str, end_date: str, notificationtypes: list
) -> StreamingResponse:
    # dates
    try:
        from_ts = _parse_date_only_utc(start_date)
        to_ts_exclusive = _parse_date_only_utc(end_date) + timedelta(days=1)
        if to_ts_exclusive <= from_ts:
            raise ValueError("'end date' must be on/after 'start date'")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # tables
    notification = Base.metadata.tables.get("notification")
    entity_universe = Base.metadata.tables.get("entity_universe")
    if notification is None or entity_universe is None:
        raise HTTPException(status_code=500, detail="Missing table(s).")

    filters = [
        notification.c.create_time >= from_ts,
        notification.c.create_time < to_ts_exclusive,
    ]

    # only add this WHERE if caller passed any values
    if notificationtypes:
        # If your Enum is a Python Enum, passing the Enum objects usually works.
        # If you stored strings, use [nt.value for nt in notificationtypes].
        filters.append(notification.c.notification_type.in_(list(dict.fromkeys(notificationtypes))))

    query = (
        select(
            entity_universe.c.external_vendor_id.label("ID"),
            notification.c.ens_id.label("ENS ID"),
            notification.c.notification_type.label("Notification Type"),
            notification.c.title.label("Title"),
            notification.c.description.label("Description"),
            notification.c.theme.label("Theme"),
            entity_universe.c.name.label("Company Name"),
            entity_universe.c.national_id.label("National ID"),
            entity_universe.c.country.label("Country"),
            notification.c.create_time.label("Create Time"),
        )
        .select_from(
            notification.join(
                entity_universe,
                entity_universe.c.ens_id == notification.c.ens_id
            )
        )
        .where(and_(*filters))
        .order_by(notification.c.create_time.desc())
    )

    print("query", query)

    # --- exactly your formatting style ---
    result = await session.execute(query)
    columns = list(result.keys())      # -> ["ID", "ENS ID", ...]
    rows = result.all()                # list[Row]
    formatted_res = [dict(zip(columns, row)) for row in rows]

    print("formatted_res__", formatted_res)
    # normalize datetimes to strings for CSV
    for d in formatted_res:
        ct = d.get("Create Time")
        if isinstance(ct, datetime):
            if ct.tzinfo:
                ct = ct.astimezone(timezone.utc)
            d["Create Time"] = ct.strftime("%Y-%m-%d %H:%M:%S")

    # write CSV from array-of-dicts
    text_buf = io.StringIO(newline="")
    writer = csv.DictWriter(text_buf, fieldnames=columns)
    writer.writeheader()
    writer.writerows(formatted_res)

    # BOM for Excel on Windows
    byte_buf = io.BytesIO()
    byte_buf.write("\ufeff".encode("utf-8"))
    byte_buf.write(text_buf.getvalue().encode("utf-8"))
    byte_buf.seek(0)

    return StreamingResponse(
        byte_buf,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="notifications_{start_date}_to_{end_date}.csv"'},
    )

# ---- helper to list ALL objects under a prefix (handles pagination) ----
def _list_all_objects(s3, bucket: str, prefix: str):
    token = None
    while True:
        kwargs = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kwargs["ContinuationToken"] = token
        resp = s3.list_objects_v2(**kwargs)
        for obj in resp.get("Contents", []):
            # skip “folders”
            if obj["Key"].endswith("/"):
                continue
            yield obj
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")

# ---- DB step: get (session_id, ens_id) pairs for given group_id ----
async def get_session_ens_pairs(
    *, session: AsyncSession, group_id: str
) -> List[Tuple[str, str]]:
    
    # SELECT DISTINCT
    # eu.last_session_id AS session_id,
    # eu.ens_id
    # FROM ens_schedule_group_mapping AS esgm
    # JOIN entity_universe AS eu
    # ON eu.ens_id = esgm.ens_id
    # WHERE esgm.group_id = :group_id
    # AND eu.last_session_id IS NOT NULL;

    md = Base.metadata
    # tables
    esgm = md.tables.get("ens_schedule_group_mapping")
    eu  = md.tables.get("entity_universe")

    if esgm is None  or eu is None:
        raise HTTPException(
            status_code=404,
            detail="Table 'ens_schedule_group_mapping' or 'session_group_mapping' or 'entity_universe' does not exist in the database schema."
        )
    pairs_stmt = (
        select(
            eu.c.last_session_id.label("session_id"),
            eu.c.ens_id.label("ens_id"),
        )
        .select_from(
            esgm.join(eu, eu.c.ens_id == esgm.c.ens_id)
        )
        .where(
            esgm.c.group_id == group_id,
            eu.c.last_session_id.isnot(None),
        )
        .distinct()
    )

    res = await session.execute(pairs_stmt)
    pairs = {(r.session_id, r.ens_id) for r in res}
    return pairs

# ---- R2 bulk download for a given group_id (no file-type filter) ----
async def r2_screener_report_bulk_download_by_source(
    session: AsyncSession, group_id: str
) -> Tuple[bytes, str]:
    try:
        # Step 1: fetch (session_id, ens_id) pairs
        pairs = await get_session_ens_pairs(session=session, group_id=group_id)
        print("pairs", pairs)
        if not pairs:
            raise HTTPException(status_code=404, detail="No (session_id, ens_id) pairs found for this group_id")

        # Step 2: zip all files for those pairs
        bucket_name = get_settings().r2_storage.storage_container_name
        s3 = get_r2_client()

        any_files = False
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:
            for session_id, ens_id in pairs:
                prefix = f"{session_id}/{ens_id}/"
                try:
                    for obj in _list_all_objects(s3, bucket_name, prefix):
                        any_files = True
                        key = obj["Key"]
                        # keep path under ENS folder, without the session level
                        relative_under_ens = key[len(prefix):]  # e.g. "subdir/file.pdf" or "file.pdf"
                        zip_path = f"{ens_id}/{relative_under_ens}"  # flatten session level
                        file_obj = s3.get_object(Bucket=bucket_name, Key=key)
                        zipf.writestr(zip_path, file_obj["Body"].read())
                except ClientError as ce:
                    # Log and continue with other pairs
                    logger.warning(f"R2 list/get failed for prefix {prefix}: {ce}")
                    continue

        if not any_files:
            raise HTTPException(status_code=404, detail="No files found in R2 for the resolved session/entity pairs")

        zip_buffer.seek(0)
        return zip_buffer.getvalue(), f"reports_{group_id}.zip"

    except ClientError as e:
        logger.error(f"Error in report_bulk_download_by_source (R2): {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to download reports")
    
