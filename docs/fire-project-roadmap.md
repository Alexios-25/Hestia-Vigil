# 🔥 Hestia — Forest Fire Detection, Analysis, Monitoring & Prediction

> *"The eternal watch. Where the forest meets the flame."*
>
> Hestia is the Greek goddess of the hearth, home, and sacred fire. She is the quiet guardian of the boundary between controlled fire (warming, sustaining) and uncontrolled fire (destruction, wildfire). She never slept, never left her post, and kept her flame burning eternally — the perfect spirit for a forest fire monitoring system.

A collection of entry points for building a forest fire tech portfolio, ordered from fastest/cheapest to most involved. Each can stand alone or stack into the next.

---

## Phase 1: Satellite Hotspot Dashboard (Hestia.Vigil) ✅ In Progress
**Module:** `hestia-vigil/`
**Status:** Bootstrap complete, implementation underway.
**Tools:** NASA FIRMS API, Python, pandas, Folium, Flask
**What it does:** Near-real-time satellite fire detections plotted on an interactive map, with configurable watch zones and Discord/email alerts for new hotspots.
**MVP time:** ~3 hours.

---

## Phase 2: Fire Weather Index Calculator (Hestia.Index)
**Module:** `hestia-index/`
**Difficulty:** Easy · **Time:** 2–3 days · **Type:** Pure Python
**Data needed:** None (implement from equations — no API calls).

Implement the Canadian Fire Weather Index system from scratch in Python. It's a well-documented set of equations combining temperature, humidity, wind, and recent precipitation to produce a daily fire danger rating (FFMC, DMC, DC, ISI, BUI, FWI).

**Why this is cool:**
- Fully self-contained — no API dependencies
- Produces a single-number "fire danger" score per day per location
- Stack directly into Phase 3 as features for the ML model
- Great teaching codebase — the math is clean and the equations are published

**Deliverables:**
- `fwi_calculator.py` — takes a CSV of daily weather, outputs daily FWI
- Unit tests for each FWI component
- Optional extension: pull live weather from NOAA/NWS API and generate FWI forecasts

**Next step after this:** Feed FWI values as features into Phase 3 (ML model).

---

## Phase 3: ML Fire Prediction Model (Hestia.Oracle) 🧠
**Module:** `hestia-oracle/`
**Difficulty:** Medium · **Time:** 1–2 weeks · **Type:** Data science / ML
**Data needed:** USDA Fire Occurrence Database, NOAA weather, LANDFIRE vegetation/fuel load layers.

Train a binary classifier that predicts whether a fire will occur in a given grid cell on a given day. Directly exercises your graduate coursework (MLE, Bayesian inference, SVMs, kernels, multivariate distributions).

**Model progression:**
1. **Baseline:** Logistic regression with Bayesian priors (ties to your Bayesian inference work)
2. **Step up:** Random Forest / XGBoost for comparison
3. **Stretch:** Spatio-temporal model — Gaussian Process with a spatial kernel (ties to your kernels coursework)
4. **Publish-worthy:** Bayesian hierarchical model with spatial random effects

**Deliverables:**
- Jupyter notebook with full ETL pipeline
- `models/` directory with serialized trained models
- Performance report (precision/recall, ROC curves, calibration plots)
- Feature importance analysis (what drives fire risk most?)

**Stack from Phase 2:** Use FWI components (FFMC, DMC, DC, ISI, BUI) as engineered features.
**Stack to Phase 5:** Use the model's risk scores as a prior in the Bayesian sensor fusion simulator.

---

## Phase 4: Smoke Detection from Webcam Feeds (Hestia.Sight) 🛰️
**Module:** `hestia-sight/`
**Difficulty:** Medium · **Time:** 1–2 weeks · **Type:** Computer vision
**Data needed:** Public webcam imagery (West Wide Fire Cam Network, highway cams, AlertWildfire).

Scrape publicly available webcam imagery and run a CNN classifier to detect smoke plumes in real time.

**Steps:**
1. Collect ~500 image samples (smoke / no smoke / haze / clouds — the last two are the hard negatives)
2. Fine-tune a small model (MobileNetV2 or EfficientNet-Lite) using PyTorch or TensorFlow
3. Deploy as a script that checks cams on a cron schedule
4. Send alerts to Discord when smoke is detected with >80% confidence

**Why it's impressive in demos:**
- Real-time video feed → ML inference → alert pipeline
- You can demo it live running against a real cam feed
- Shows end-to-end computer vision skills (data collection → training → deployment)

**Deliverables:**
- Trained model + inference script
- Dataset with labels
- Live demo capability (cron-based or web-based viewer)

---

## Phase 5: Ground Sensor Network Simulator (Hestia.Nodes) 📡
**Module:** `hestia-nodes/`
**Difficulty:** Medium · **Time:** 2–4 weeks · **Type:** Simulation + embedded prototype
**Tools:** Python (simulation), STM32 + sensors (hardware prototype — optional)

Simulate a LoRa/mesh network of ground sensors scattered across a forest model. Build a centralized "fusion" node that does probabilistic Bayesian belief updating as sensor readings arrive.

**Simulation features:**
- Agent-based or event-driven simulation of N sensor nodes
- Each sensor reports: temperature, humidity, CO level, PM2.5 (smoke particulate)
- Fire events are injected randomly; sensor readings spike in a diffusion pattern
- Central node fuses readings using Bayesian updating to estimate P(fire | readings)
- Visualization: terminal curses or web dashboard showing belief state over time

**Hardware prototype (stretch goal, leverages your STM32 experience):**
- Flash real code to an STM32 + BME280 (temp/humidity) + MQ-2 (smoke/CO) sensor node
- Connect via LoRa or WiFi to a central gateway
- Same Bayesian fusion runs on the gateway (Python on a Pi, or C on STM32)

**Stack from Phase 3:** Use ML model risk scores as the Bayesian prior.
**Stack to Phase 6:** The optimized fusion algorithm runs on the drone as it collects thermal data.

**Deliverables:**
- Sensor network simulator (Python, well-documented)
- Bayesian fusion module with unit tests
- Web dashboard showing real-time belief state
- (Optional) STM32 firmware for a physical sensor node

---

## Phase 6: Drone-Based Thermal Anomaly Monitor (Hestia.Wing) 🚁
**Module:** `hestia-wing/`
**Difficulty:** Hard · **Time:** Weeks to months · **Type:** Robotics / embedded
**Tools:** FPV drone, FLIR Lepton or MLX90640 thermal sensor, Jetson Orin Nano or Raspberry Pi, your FPV + Jetson Orin AGX experience.

Mount a thermal sensor on a drone (or ground rig), stream telemetry, and build a pipeline that detects and reports heat anomalies.

**MVP:**
- Thermal sensor logs imagery / temperature grids locally
- On landing, run anomaly detection (island of pixels > threshold, or statistical outlier)
- Overlay thermal data on a GIS map

**Stretch goals:**
- Real-time streaming over MAVLink / WiFi
- Autonomously patrol a predefined grid waypoint path (GPS waypoint navigation)
- Onboard thermal analysis aboard Jetson Orin Nano (CUDA-accelerated, leveraging your GPU skills)
- Fuse thermal detections with satellite data (Phase 1) for cross-validation

**Why this is the capstone:**
- Combines embedded systems, CUDA/GPU, ROS/robotics, GIS, and ML
- Shows end-to-end hardware-to-dashboard pipeline
- Extremely impressive in a portfolio or interview demo
- Directly relevant to wildfire robotics research

**Deliverables:**
- Thermal logging + anomaly detection pipeline
- GIS overlay visualizations
- (Optional) Autonomous patrol firmware
- (Optional) Real-time streaming + on-board inference demo

---

## Suggested Sequencing

```
Phase 1: Hestia.Vigil (FIRMS Dashboard)  ~3h     Immediate gratification, learn the data landscape
Phase 2: Hestia.Index (FWI Calculator)    ~2–3d   Clean math, feature engineering prep
Phase 3: Hestia.Oracle (ML Prediction)    ~1–2w   Directly leverages your graduate coursework
Phase 4: Hestia.Sight (Smoke CNN)        ~1–2w   Computer vision portfolio piece
Phase 5: Hestia.Nodes (Sensor Sim)       ~2–4w   Bayesian fusion + embedded prototype
Phase 6: Hestia.Wing (Drone Thermal)     ~1–3m   Capstone — all skills combined
```

---

## Skills Demonstrated (for your portfolio / resume)

| Phase | Module | Skills Shown |
|-------|--------|-------------|
| 1 — Vigil | API integration, geospatial viz, web dev, alerting |
| 2 — Index | Numerical methods, clean code, testing |
| 3 — Oracle | MLE, Bayesian inference, kernels, feature engineering, model evaluation |
| 4 — Sight | Computer vision, PyTorch/TF, data collection, deployment |
| 5 — Nodes | Agent-based simulation, Bayesian updating, embedded C (STM32) |
| 6 — Wing | Robotics, CUDA/GPU, ROS, GIS, real-time systems |

**Total portfolio arc:** Data pipeline → ML → Computer vision → Sensor fusion → Robotics. Covers the full stack of wildfire tech.

---

*Last updated: 2026-06-10 — Phase 1 (Hestia.Vigil) bootstrap complete, implementation underway.*