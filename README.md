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
