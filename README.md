# EKZ Tariff

Home Assistant custom integration for **raw EKZ tariff data from EKZ**.

Repository: `cnc-lasercraft/ha-ekz-tariff`

## What it provides

- myEKZ OAuth2 login
- EMS link status and linking workflow
- Personal tariffs from `/customerTariffs`
- Public baseline tariffs from `/tariffs`
- 15-minute raw price slots
- Current raw price sensors for:
  - electricity
  - grid
  - regional fees
  - integrated / all-in
- Publication timestamps
- Diagnostics download for support and debugging
- Internal provider API for other integrations such as **Tariff Saver**

## What it does not do

This integration intentionally provides **raw data only**. It does **not** calculate:

- costs
- savings
- cheapest windows
- charging optimization
- scoring
- historical analysis

That logic belongs in **Tariff Saver**.

## Installation via HACS

1. Add this repository as a custom repository in HACS.
2. Install **EKZ Tariff**.
3. Restart Home Assistant.
4. Add the integration from **Settings → Devices & services**.

## Setup

During setup you provide:

- a name for the config entry
- the EKZ linking redirect URL
- the public baseline tariff name (default: `electricity_standard`)
- the daily publish time used for refresh scheduling (default: `18:15`)
- OAuth login for myEKZ

## Diagnostics

Home Assistant diagnostics include a compact support summary with:

- link status
- last successful API fetch
- active and baseline slot counts
- publication timestamps
- current component keys
- shortened EMS instance id

Tokens and secrets are redacted.

## Notes

- The full 24h / 96-slot tariff data stays **inside the coordinator** and is **not** pushed into large sensor attributes.
- This avoids Recorder bloat while still making the raw slots available to **Tariff Saver** through the internal provider API.
- The integration refreshes daily at the configured publish time and retries automatically until a complete local-day slot set is available.
