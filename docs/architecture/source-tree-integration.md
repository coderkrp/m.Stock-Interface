# Source Tree Integration

### New File Organization

```plaintext
project-root/
├── src/
│   ├── __init__.py
│   ├── main.py         # FastAPI app and routes
│   ├── models.py       # Pydantic models
│   ├── settings.py     # Pydantic settings
│   └── security.py     # Throttling middleware
├── tests/
│   ├── __init__.py
│   └── test_main.py    # Tests for the API
└── requirements.txt
```
