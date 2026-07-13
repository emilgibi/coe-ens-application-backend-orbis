# COE-ENS-APPLICATION-BACKEND

## 🚀 Overview
This is a backend application built with **FastAPI**, designed for high performance and ease of use. It leverages **Alembic** for database migrations and **Poetry** for dependency management. The application follows a modular structure, ensuring maintainability and scalability.

---

## 📁 Project Structure
### 📂 Key Directories
- **alembic/** - Database versioning and migrations.
- **app/**
  - **api/** - Route handlers and endpoints.
  - **core/** - Configuration and core functionalities.
  - **schemas/** - Pydantic models for data validation.
  - **models.py** - SQLAlchemy models representing database tables.
  - **main.py** - Application entry point.
  - **tests/** - Unit and integration tests.

---

## 🛠️ Requirements
- **Python** 3.12
- **Poetry** (for dependency management)
- **Docker** (optional, for containerized deployment)

---

## ⚙️ Installation

1. **Clone the repository:**
    ```bash
    git clone https://github.com/20230028426_EYGS/coe-ens-application-backend.git
    cd coe-ens-application-backend
    ```

2. **Create a virtual environment:**
    ```bash
    python -m venv venv
    ```

3. **Activate the virtual environment:**
    - On **Windows**:
      ```bash
      venv\Scripts\activate
      ```
    - On **macOS/Linux**:
      ```bash
      source venv/bin/activate
      ```

4. **Install Poetry:**
    ```bash
    pip install poetry
    ```

5. **Install dependencies:**
    ```bash
    poetry install
    ```

6. **Apply database migrations using Alembic:**
    ```bash
    alembic upgrade head
    ```

7. **Run the application:**
    ```bash
    uvicorn app.main:app --port 8000 --reload
    ```

8. **Access the application:**
    ```
    http://127.0.0.1:8000
    ```
9. **Run in 2 seprate Terminal**
    ```
    celery -A app.core.utils.celery_worker worker --loglevel=info --pool=solo
    ```

    ```
    celery -A app.core.utils.celery_worker beat --loglevel=info
    ```
9. **Running tests**

```bash
# see all pytest configuration flags in pyproject.toml
pytest
```

---

## License

This project is licensed under the MIT License. See the `LICENSE` file for details.

---

## Contact

For questions or support, reach out at: [riddhi.singh@in.ey.com](mailto:riddhi.singh@in.ey.com).