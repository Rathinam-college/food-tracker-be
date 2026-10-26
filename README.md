# Food Tracker Backend

Django REST Framework backend service for the Rathinam Food Tracker application.

## Requirements
* Python 3.10+
* PostgreSQL
* Dependencies listed in `requirements.txt`

## Getting Started

1. Set up a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Setup environment variables:
   ```bash
   cp .env.example .env
   # Edit .env with your PostgreSQL credentials
   ```

4. Run migrations:
   ```bash
   python manage.py migrate
   ```

5. Start development server:
   ```bash
   python manage.py runserver
   ```
