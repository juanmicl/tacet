# Umbra: Passive Drone Detection Layer

Low-cost, receive-only drone detection for critical infrastructure. RF
signature analysis, acoustic arrays, and passive radar, fused by a Bayesian
multi-target tracker. The system emits nothing, jams nothing, and does not
assume a prime-contractor budget.

> Status: early research and prototyping. Current phase: bench
> characterization. Everything here is receive-only and legal for private
> operators in the EU.

---

## Why

Most counter-drone products fail at three things:

1. Silent drones. Fiber-optic and fully autonomous UAVs emit nothing on RF,
   so RF-only detection (the cheap approach sold today) is blind to exactly
   the threat that worries security teams.
2. Price. Airport-grade systems cost six figures. Prisons, solar farms,
   ports, and data centers have real drone problems and no airport budget.
3. Legality. In the EU, private operators may not jam or intercept. The
   legal market is passive detection, which is also the stealthiest
   architecture: an undetectable sensor cannot be targeted.

Umbra is a layered answer: three sensing modalities, each covering the blind
spot of the others, fused into tracks with quantified uncertainty.

## Architecture

```mermaid
flowchart LR
    RF[RF Signature Node<br/>HackRF / RTL-SDR<br/>2.4 GHz ELRS · 5.8 GHz OFDM] --> F((Fusion<br/>Tracker<br/>GNN + EKF/UKF))
    AC[Acoustic Node<br/>MEMS mic array + ESP32<br/>bearing + rotor harmonics] --> F
    PR[Passive Radar<br/>Pluto+ (AD9363) coherent dual-RX, DVB-T illuminators<br/>range + Doppler + micro-Doppler] --> F
    RID[Remote ID Sniffers<br/>ESP32 × N · BLE / WiFi NaN] --> F
    F --> D[SQLite detections + dashboard]
    D --> A[Alerts · SMS / MQTT]
```

| Module | Detects | Range (target) | Cost/node |
| --- | --- | --- | --- |
| RF signature | Control & video links (ELRS, DJI OFDM, analog) | 0.3–2 km | €60–400 |
| Acoustic array | Rotor harmonics; works against RF-silent drones | 100–300 m | €30 |
| Passive radar (DVB-T) | Kinematics of any airborne reflector | 0.5–3 km | €180 |
| Remote ID sniffer | Compliant UAVs (cooperative layer) | 100–500 m | €8 |
| Fusion tracker | Single coherent track from all sensors (Stone Soup) | — | — |

## Roadmap

| Phase | Scope | Deliverable |
| --- | --- | --- |
| P0, Bench (now) | RF signatures of own FPV drones (ELRS 2.4, DJI O4 5.8), SNR-vs-range curves | `dataset v0.1` + measured detection ranges |
| P1, Baselines | Pipeline on public datasets (DroneDetect, DroneRF): loader → features → model → ROC | Reproducible benchmark notebook |
| P2, Acoustic node | 4–8 MEMS mic array, SRP-PHAT bearing, rotor-band classifier | Acoustic detection vs. own drones |
| P3, Passive radar | Pluto+ + DVB-T reference/surveillance, clutter cancellation, CAF, CFAR | Range-Doppler detections of own drones |
| P4, Fusion | GNN data association, EKF/UKF tracks from all modalities | Single fused track, live demo |
| P5, Mesh | 2+ autonomous nodes (Pi + RTL-SDR), time-synced detections, TDOA geolocation | Multi-node geolocalized demo |
| P6, Pilot | Real-site deployment, alerting, ops feedback | Field report + metrics |

## Hardware (current lab)

- HackRF One + dual-band 2.4/5.8 antenna + FPV 5.8 antennas
- 2× Raspberry Pi 3B+ (node brains) · ESP32 (Remote ID + acoustic DSP)
- Planned: 2× RTL-SDR (~€60), MEMS I2S mics (~€20), and a Pluto+ (AD9363) as
  the radar unit — cheaper than a KrakenSDR and, more importantly, it can
  actually capture a full 8 MHz DVB-T channel.
- Test targets: a 5" FPV quad (ELRS + DJI O4) and a 250 g FPV quad, both
  owned and legal

## Data strategy

- Public baselines: DroneDetect (IEEE DataPort) and DroneRF, used with
  proper attribution for pipeline validation and benchmarks.
- Own dataset: labeled IQ and acoustic recordings of modern FPV links
  (ELRS, DJI O4) at known distances, powers, and environments. To our
  knowledge no public dataset covers these; release policy is TBD.
- All recordings follow a strict manifest schema (device, protocol,
  distance, TX power, antenna, environment, LOS/NLOS, license-consistent
  source).

```
repo/
├── src/umbra/    # package code: loaders (cs8/cs16/f32 → complex64,
│                 # manifest ingestion), dsp (features, detectors),
│                 # trackers + nodes orchestration (later)
├── tests/        # synthetic self-tests (import umbra.*)
├── notebooks/    # experiments (numbered, reproducible)
├── docs/         # experiment reports
└── data/         # gitignored; manifest + checksums tracked
```

## Non-goals

- No neutralization (jamming or spoofing): illegal for private EU
  operators, and not our layer.
- No intercepting communication content; detection of emitters only.
- No weapons integration.

## License

Code: MIT, from day one (licenses matter). Dataset licenses: TBD, stated
per release.

## Legal notice

Umbra is receive-only passive sensing. It does not jam, decrypt, or
intercept content. Operators remain responsible for compliance with local
radio and privacy law.
