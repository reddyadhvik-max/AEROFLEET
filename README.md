# AEROFLEET V2 Modular Pipeline

AEROFLEET is an advanced, edge-based unified telematics platform designed for real-time fleet monitoring, driver safety, and event recording. It leverages computer vision running locally on the vehicle's edge device to instantly detect driver distraction, phone usage, and drowsiness, while actively verifying driver identity to prevent unauthorized vehicle operation.

## Key Features
- **Facial Recognition Security**: Active, localized driver verification using 128-d face encodings. Restricts operation to max 3 enrolled drivers per truck.
- **Safety Telematics**: Real-time detection of drowsiness (via eye aspect ratio) and distracted driving (phone usage) using YOLOv8 and MediaPipe.
- **Resilient Cloud Sync**: Stores telemetry and video events locally (`fleet.db` and `.mp4` files) and asynchronously uploads them to the cloud via a background watchdog when connectivity is restored.
- **Unified Portal**: Dual-role architecture providing a live dashboard for the driver (navigation, live cameras) and a comprehensive control center for the fleet admin (analytics, driver enrollment, fleet map).

## Prerequisites
- **Python 3.9+** (64-bit recommended)
- **Git**
- A webcam connected to the system (for live face/drowsiness/phone detection).

## Setup & Installation

### 1. Clone the Repository
Open your terminal and clone the repository to your local machine:
```bash
git clone https://github.com/reddyadhvik-max/AEROFLEET.git
cd AEROFLEET/V2_Modular_Pipeline
```

### 2. Install Dependencies
Ensure you are in the `V2_Modular_Pipeline` directory and install the required Python packages:
```bash
pip install -r requirements.txt
```
*(Note: `face_recognition` requires CMake and a C++ compiler to be installed on Windows. If you encounter errors installing `dlib`, you must install Visual Studio Build Tools with C++ workloads first.)*

### 3. Initialize the System
The system is orchestrated by a PowerShell script that launches both the FastAPI backend and the asynchronous upload watchdog.

Run the startup script:
```powershell
.\start_v2.ps1
```

### 4. Access the Portals
Once the server is running, open your web browser:
- **Unified Login**: `http://localhost:8000/index.html`

**Default Admin Credentials (if fresh database):**
- **Username:** `admin`
- **Password:** `admin`

## Architecture overview
- **main.py**: The core FastAPI server handling routing, the background hardware pipeline (camera access, ML inference loop), and WebSocket streams.
- **watchdog.py**: A completely decoupled background service that monitors the local edge storage and pushes recorded violation clips to the central cloud.
- **driver_certification.py**: Manages facial encodings and driver verification using `face_recognition`.
- **drowsiness_detector.py / phone_detector.py**: Modular inference engines leveraging MediaPipe Face Mesh and YOLOv8n.
- **dashboard/**: Contains the HTML/JS/CSS for the driver and admin interfaces.
