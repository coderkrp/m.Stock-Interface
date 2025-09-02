# Infrastructure and Deployment Integration

*   **Deployment Approach:** The deployment process will remain manual. After the refactoring, the command to run the application will be updated to `uvicorn src.main:app`.
*   **Rollback Method:** In case of any issues after deployment, the application can be rolled back to a previous stable state using Git version control.
