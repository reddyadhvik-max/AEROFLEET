Write-Host "Starting Docker containers..."
& "C:\Program Files\Docker\Docker\resources\bin\docker.exe" compose up -d

Write-Host "Waiting 15 seconds for PostgreSQL to accept connections..."
Start-Sleep -Seconds 15

Write-Host "Initializing Database Schema..."
C:\Users\reddy\OneDrive\Desktop\AEROFLEET\venv\Scripts\python.exe C:\Users\reddy\OneDrive\Desktop\AEROFLEET\backend\database.py

Write-Host "Starting API Server..."
Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -NoExit -Command `"cd C:\Users\reddy\OneDrive\Desktop\AEROFLEET; .\venv\Scripts\activate; cd backend; uvicorn main:app --host 0.0.0.0 --port 8000 --reload`""

Write-Host "Starting Stream Processor..."
Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -NoExit -Command `"cd C:\Users\reddy\OneDrive\Desktop\AEROFLEET; .\venv\Scripts\activate; cd backend; python processor.py`""

Write-Host "Starting Physics Simulator..."
Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -NoExit -Command `"cd C:\Users\reddy\OneDrive\Desktop\AEROFLEET; .\venv\Scripts\activate; cd simulator; python truck_simulator.py`""

Write-Host "Starting Camera Buffer..."
Start-Process powershell -ArgumentList "-ExecutionPolicy Bypass -NoExit -Command `"cd C:\Users\reddy\OneDrive\Desktop\AEROFLEET; .\venv\Scripts\activate; cd camera; python camera_buffer.py`""

Write-Host "Done! You can close this terminal. Your Dashboard is available at http://localhost:8000"
