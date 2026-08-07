# Jobyn AI Backend Engineering Rules

You are working on Jobyn AI v2.

Architecture:
- Backend: FastAPI
- ORM: SQLAlchemy
- Database: SQLite (development)
- Frontend: Next.js
- Authentication: JWT
- AI services: Resume parsing, candidate profiling, job matching

Engineering principles:

1. Never make large rewrites.
2. Always inspect existing architecture before changing code.
3. Preserve separation of concerns:

Routes:
- HTTP handling
- validation
- status codes
- transaction boundaries

Services:
- business logic
- orchestration
- validation rules

Repositories:
- database queries only

Models:
- database structure only

Schemas:
- API contracts only

4. Database rules:
- Always handle commit and rollback explicitly.
- Never leave transactions open.
- Never assume flush means persistence.

5. Authentication rules:
- Passwords must always be hashed.
- Never store plain passwords.
- Duplicate emails must return HTTP 409.
- Login must work immediately after registration.

6. Testing rules:
Before declaring a fix complete:
- run pytest
- verify affected endpoints
- add regression tests

7. Code quality:
- use type hints
- add meaningful exceptions
- avoid hidden side effects
- keep APIs backward compatible