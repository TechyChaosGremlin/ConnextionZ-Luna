# Dependency Installation Guide (Improved)

## Overview

This guide explains how to install dependencies for the ConnextionZ platform using the improved setup scripts.

The JWT backend uses Passlib 1.7.4 with `bcrypt>=4.1.0,<5`. Keep this constraint
in both backend requirements files: bcrypt 5 rejects Passlib's backend compatibility
probe, preventing even short valid passwords from being hashed.

## Quick Start

### Windows Users

**Option 1: Batch file (Easiest)**
```batch
.\install.bat
```
This will prompt you to choose what to install.

**Option 2: PowerShell script (Improved)**
```powershell
# Full setup with all options
.\setup-improved.ps1

# Skip Docker (if not needed)
.\setup-improved.ps1 -SkipDocker

# Force reinstall
.\setup-improved.ps1 -Force

# Backend only
.\setup-improved.ps1 -BackendOnly

# Frontend only
.\setup-improved.ps1 -FrontendOnly
```

**Option 3: Quick installer**
```powershell
# Install everything
.\install-dependencies.ps1 -All

# Install backend only
.\install-dependencies.ps1 -Backend

# Install frontend only
.\install-dependencies.ps1 -Frontend

# Force reinstall
.\install-dependencies.ps1 -All -Force
```

### Unix/macOS Users

**Option 1: Bash script (Improved)**
```bash
chmod +x setup-improved.sh

# Full setup
./setup-improved.sh

# Skip Docker
./setup-improved.sh --skip-docker

# Force reinstall
./setup-improved.sh --force

# Backend only
./setup-improved.sh --backend-only

# Frontend only
./setup-improved.sh --frontend-only
```

**Option 2: Makefile**
```bash
# Check prerequisites
make check-prereqs

# Install everything
make setup

# Force reinstall
make setup-force

# Backend only
make backend-setup

# Frontend only
make frontend-setup
```

## What's Improved

### 1. **Batch Installation (Python)**
Instead of installing all Python packages at once, the improved scripts install them in batches:
- Batch 1: Core (FastAPI, SQLAlchemy, Pydantic)
- Batch 2: Database (Alembic, pgvector, auth)
- Batch 3: Caching (Redis)
- Batch 4: GraphQL (Strawberry)
- Batch 5: Data science (NumPy, Pandas, Scikit-learn)
- Batch 6: ML (PyTorch)
- Batch 7: LLM (Transformers, OpenAI, Anthropic)
- Batch 8: AWS & Monitoring
- Batch 9: Testing & Code Quality

**Benefit**: If one batch fails, you can retry just that batch instead of the entire installation.

### 2. **Retry Logic**
All scripts now have retry logic:
- Python packages: 3 retries per batch
- npm packages: 3 retries with cleanup between attempts
- Docker containers: Wait for healthy status

### 3. **Better Error Handling**
- Prerequisite checks before installation
- Clear error messages with suggested fixes
- Graceful handling of interrupted installations
- Permission error detection and recovery

### 4. **Force Reinstall Option**
Added `-Force` (PowerShell) and `--force` (bash) flags to:
- Delete and recreate Python virtual environment
- Remove `node_modules` and `package-lock.json`
- Clean Docker volumes

### 5. **Skip Options**
- `-SkipDocker` / `--skip-docker`: Don't start Docker containers
- `-BackendOnly` / `--backend-only`: Install only Python dependencies
- `-FrontendOnly` / `--frontend-only`: Install only Node.js dependencies

### 6. **Legacy Peer Deps**
The frontend installation now uses `--legacy-peer-deps` by default to avoid dependency conflicts (like the `react-scripts` vs TypeScript issue we encountered).

## Troubleshooting

### Python Installation Fails

**Problem**: `pip install` fails with permission errors or network timeout.

**Solution**:
```powershell
# Use the improved script with retry logic
.\setup-improved.ps1 -BackendOnly

# Or manually install in batches
cd app
.\venv\Scripts\Activate.ps1
pip install fastapi uvicorn sqlalchemy  # Batch 1
pip install alembic pgvector                 # Batch 2
# ... continue with other batches
```

### npm Installation Fails

**Problem**: `npm install` fails with peer dependency conflicts or permission errors.

**Solution**:
```powershell
# Use legacy peer deps
npm install --legacy-peer-deps

# Or use the improved script
.\setup-improved.ps1 -FrontendOnly

# Or manually fix permissions (run as administrator)
Remove-Item -Recurse -Force node_modules
npm install --legacy-peer-deps
```

### Docker Containers Won't Start

**Problem**: Docker daemon not running or containers fail to start.

**Solution**:
1. Start Docker Desktop
2. Wait for it to fully load
3. Run: `docker-compose -f docker/docker-compose.yml up -d`
4. Check logs: `docker-compose -f docker/docker-compose.yml logs -f`

## Verification

After installation, verify everything works:

### Backend Verification
```powershell
cd app
.\venv\Scripts\Activate.ps1
python -c "import fastapi; import sqlalchemy; import torch; print('✓ Backend dependencies OK')"
```

### Frontend Verification
```powershell
npm list --depth=0
npm run dev  # Should start dev server on http://localhost:3000
```

### Docker Verification
```powershell
docker-compose -f docker/docker-compose.yml ps
# All containers should show "Up (healthy)"
```

## Next Steps

After successful installation:

1. **Configure environment**:
   ```powershell
   copy .env.example .env.local
   # Edit .env.local with your settings
   ```

2. **Run database migrations**:
   ```powershell
   cd app
   .\venv\Scripts\Activate.ps1
   alembic upgrade head
   ```

3. **Start development servers**:
   ```powershell
   # Terminal 1: Backend
   cd app
   .\venv\Scripts\Activate.ps1
   uvicorn main:app --reload --host 0.0.0.0 --port 8000
   
   # Terminal 2: Frontend
   npm run dev
   ```

4. **Access the application**:
   - Backend API: http://localhost:8000
   - API Docs: http://localhost:8000/docs
   - Frontend: http://localhost:3000
   - GraphQL Playground: http://localhost:8000/graphql

## Need Help?

If you still encounter issues:

1. Check the [troubleshooting guide](./README_SETUP.md#troubleshooting)
2. Open an issue on GitHub
3. Contact the maintainers

---

**Happy coding! 🚀**
