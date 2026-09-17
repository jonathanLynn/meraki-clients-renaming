# Meraki MAC-to-hostname client renamer

This script searches a Cisco Meraki organization for each MAC address in a Python list and sets the client's Dashboard description/display name to the corresponding hostname.

It is a **dry run by default**. No Meraki changes are made unless you add `--apply`.

## Requirements

- Python 3.10 or later
- A Meraki Dashboard API key
- Your Meraki organization ID
- API permission to read organization clients and client policies and write client configuration

The script uses only the Python standard library; there are no packages to install.

## 1. Export device names and MAC addresses from Intune

For a quick manual export:

1. Sign in to the [Microsoft Intune admin center](https://intune.microsoft.com/).
2. Go to **Devices → All devices**.
3. Apply any platform or ownership filters you need.
4. Select **Export** above the device list and download the CSV.
5. In the exported file, use **Device name** together with either **Wi-Fi MAC** or **EthernetMAC**.

![Illustration of exporting the All devices inventory from Intune](assets/02-export-from-intune.svg)

Microsoft identifies the All devices export as the `DevicesWithInventory` report. Its documented columns include `DeviceName`, `WifiMacAddress`, and `EthernetMAC`. If the portal export does not contain the columns you need, use the [Intune report export API](https://learn.microsoft.com/en-us/intune/device-management/reports/export-graph-apis) and explicitly select them:

```json
{
  "reportName": "DevicesWithInventory",
  "format": "csv",
  "select": ["DeviceName", "WifiMacAddress", "EthernetMAC"]
}
```

The export API endpoint is:

```text
POST https://graph.microsoft.com/beta/deviceManagement/reports/exportJobs
```

The job returns a download URL after its status becomes `completed`. Microsoft recommends explicitly selecting required columns instead of relying on report defaults. See the [available Intune report properties](https://learn.microsoft.com/en-us/intune/device-management/reports/ref-graph-available-reports#deviceswithinventory).

> MAC reporting depends on the platform and enrollment type. Some devices may have a blank Wi-Fi or Ethernet MAC, and devices using private/randomized Wi-Fi MAC addresses might not match the address observed by Meraki.

## 2. Add the devices to the script

Open `rename_meraki_clients.py` and edit `DEVICES` near the top:

```python
DEVICES = [
    {"mac": "54:14:f3:69:66:2b", "hostname": "LAPTOP-001"},
    {"mac": "A0-B1-C2-D3-E4-F5", "hostname": "LAPTOP-002"},
]
```

![Illustration of editing the DEVICES list](assets/01-edit-device-list.svg)

MAC addresses may contain colons, hyphens, dots, or no separators. Duplicate mappings to the same hostname are collapsed; conflicting hostnames for the same MAC stop the script before any changes are made.

## 3. Configure credentials

### macOS or Linux

Open Terminal in the script directory:

```bash
export MERAKI_DASHBOARD_API_KEY='your-api-key'
export MERAKI_ORGANIZATION_ID='your-organization-id'
```

### Windows PowerShell

```powershell
$env:MERAKI_DASHBOARD_API_KEY = 'your-api-key'
$env:MERAKI_ORGANIZATION_ID = 'your-organization-id'
```

Keep the API key out of the Python file and source control.

## 4. Preview the changes

Run without `--apply`:

```bash
python3 rename_meraki_clients.py
```

On Windows, the command may be:

```powershell
py rename_meraki_clients.py
```

Review every `would_update` entry. A typical dry run looks like this:

```json
{
  "mode": "dry-run",
  "counts": {"would_update": 1},
  "results": [
    {
      "mac": "54:14:f3:69:66:2b",
      "hostname": "LAPTOP-001",
      "oldName": "Windows client",
      "status": "would_update"
    }
  ]
}
```

## 5. Apply the changes

After confirming that the mappings are correct:

```bash
python3 rename_meraki_clients.py --apply
```

![Illustration of dry-run and apply commands](assets/03-run-script.svg)

## SSL inspection and `--insecure`

If a trusted corporate TLS-inspection proxy causes certificate errors, you can temporarily disable certificate verification:

```bash
# Dry run
python3 rename_meraki_clients.py --insecure

# Apply changes
python3 rename_meraki_clients.py --insecure --apply
```

`--insecure` removes protection against an impersonated API server. Installing your organization's trusted CA certificate is the safer long-term solution.

## Result statuses

| Status | Meaning |
| --- | --- |
| `would_update` | Dry-run preview; no change was made. |
| `updated` | The Meraki client name was changed. |
| `unchanged` | The existing description already matches the hostname. |
| `not_found` | Meraki did not return a matching client/network record. |
| `failed` | The API request or policy conversion failed; see `reason`. |

If a client was observed in multiple Meraki networks, the script processes each returned network record.

## Why the script reads the current policy

Meraki assigns the client display name through the client provisioning endpoint, which also requires a policy value. The script reads and reuses the client's current policy so a rename does not silently reset its access policy.

## Troubleshooting

- **HTTP 401/403:** Check the API key and its organization permissions.
- **`not_found`:** Confirm the MAC in Intune matches the address Meraki observes, including private/randomized Wi-Fi MAC behavior.
- **SSL certificate failure:** Install the corporate CA where possible, or use `--insecure` temporarily.
- **HTTP 400 involving `perPage`:** Use the current script; it uses the organization search endpoint's supported value of `5`.
- **Nothing changes:** Confirm you supplied `--apply`; dry run intentionally makes no changes.
