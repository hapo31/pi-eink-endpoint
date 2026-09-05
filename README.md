# README

## git submodule

```
git submodule init
git submodule update
```

```
git -C pi_eink_endpoint/waveshare_e_paper config core.sparsecheckout true
```

## Raspberry Pi service

The application uses the system Python and packages installed with `apt`.
After deploying to `/home/<user>/eink-endpoint/dist`, install the systemd
template unit and substitute the login user as its instance name:

```bash
sudo cp pi-eink-endpoint@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pi-eink-endpoint@$USER
```

Check the service with:

```bash
systemctl status pi-eink-endpoint@$USER
journalctl -u pi-eink-endpoint@$USER -f
```

## Request handling

`POST /text` (JSON) and `POST /image` (image bytes) return HTTP `202 Accepted`
with `{"message": "E-ink update queued"}` once the request body has been received
and queued, without waiting for the display refresh. Invalid JSON still returns
HTTP `400 Bad Request`.

Both endpoints share one in-memory FIFO queue. A single worker renders requests
in enqueue order, including requests received while another refresh is running.
Rendering failures are logged and the worker continues with the next request;
`202` confirms acceptance, not successful rendering. Queued requests are lost
if the process is terminated or restarted. Run only one server process per display.
