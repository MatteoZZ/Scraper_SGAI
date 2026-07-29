"""
Servizio VPN per download MEF.

- Legge IP pubblico
- Ruota Proton (script PowerShell) o un comando custom
- Usato da AUTO-HEAL quando l'IP e' bruciato (403 a raffica / ricerca KO post-heal)

Uso da CLI:
  python mef_vpn.py status
  python mef_vpn.py rotate --provider proton
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

ROOT = Path(__file__).resolve().parent
PROTON_ROTATE_PS1 = ROOT / "rotate_proton_vpn.ps1"
STATE_PATH = ROOT / "mef_vpn_state.json"

LogFn = Callable[[str], None]


def _default_log(msg: str) -> None:
    print(msg, flush=True)


def get_public_ip(timeout: float = 12.0) -> str:
    """IP pubblico attuale (via VPN se connessa)."""
    urls = (
        "https://api.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    )
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "mef-vpn/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ip = resp.read().decode("utf-8", errors="ignore").strip()
                if ip and " " not in ip and len(ip) < 64:
                    return ip
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
    return ""


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(data: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


@dataclass
class RotateResult:
    ok: bool
    old_ip: str
    new_ip: str
    detail: str = ""

    @property
    def ip_changed(self) -> bool:
        return bool(self.old_ip and self.new_ip and self.old_ip != self.new_ip)


class VpnService:
    """Interfaccia rotazione VPN."""

    name = "base"

    def __init__(self, log: LogFn | None = None) -> None:
        self.log = log or _default_log

    def status(self) -> dict:
        ip = get_public_ip()
        return {"provider": self.name, "ip": ip, "ts": time.time()}

    def rotate(self, *, wait_ip_change: bool = True, timeout: float = 120.0) -> RotateResult:
        raise NotImplementedError


class ShellVpnService(VpnService):
    """Esegue un comando shell (es. nordvpn connect)."""

    name = "shell"

    def __init__(self, command: str, log: LogFn | None = None) -> None:
        super().__init__(log)
        self.command = command.strip()

    def rotate(self, *, wait_ip_change: bool = True, timeout: float = 120.0) -> RotateResult:
        old_ip = get_public_ip()
        self.log(f"[VPN:{self.name}] IP prima: {old_ip or '?'}")
        self.log(f"[VPN:{self.name}] Eseguo: {self.command}")
        try:
            proc = subprocess.run(
                self.command,
                shell=True,
                timeout=max(30, int(timeout)),
                check=False,
                capture_output=True,
                text=True,
            )
            detail = (proc.stdout or "")[-500:] + (proc.stderr or "")[-300:]
        except Exception as exc:
            return RotateResult(False, old_ip, old_ip, str(exc))

        new_ip = old_ip
        if wait_ip_change:
            new_ip = self._wait_new_ip(old_ip, timeout=timeout)
        else:
            time.sleep(8)
            new_ip = get_public_ip() or old_ip

        ok = bool(new_ip) and (new_ip != old_ip if old_ip else True)
        self.log(f"[VPN:{self.name}] IP dopo: {new_ip or '?'} changed={ok}")
        save_state(
            {
                "provider": self.name,
                "old_ip": old_ip,
                "new_ip": new_ip,
                "ok": ok,
                "ts": time.time(),
            }
        )
        return RotateResult(ok, old_ip, new_ip or "", detail.strip())

    def _wait_new_ip(self, old_ip: str, timeout: float) -> str:
        deadline = time.time() + timeout
        last = old_ip
        while time.time() < deadline:
            time.sleep(4)
            cur = get_public_ip()
            if cur:
                last = cur
                if old_ip and cur != old_ip:
                    return cur
                if not old_ip:
                    return cur
        return last


class ProtonVpnService(VpnService):
    """
    Proton VPN Windows via rotate_proton_vpn.ps1
    (forza ProTUN down + riavvio client; Free spesso riusa lo stesso server).
    """

    name = "proton"

    def __init__(self, script: Path | None = None, log: LogFn | None = None) -> None:
        super().__init__(log)
        self.script = (script or PROTON_ROTATE_PS1).resolve()

    def rotate(self, *, wait_ip_change: bool = True, timeout: float = 100.0) -> RotateResult:
        if not self.script.exists():
            return RotateResult(
                False, get_public_ip(), "", f"Manca script: {self.script}"
            )
        old_ip = get_public_ip()
        self.log(f"[VPN:proton] IP prima: {old_ip or '?'}")
        # Lo script gia' aspetta il cambio IP: niente doppio wait in Python
        inner_timeout = max(60, int(timeout) - 10)
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script),
            "-OldIp",
            old_ip or "",
            "-TimeoutSec",
            str(inner_timeout),
        ]
        detail = ""
        try:
            proc = subprocess.run(
                cmd,
                timeout=max(70, int(timeout) + 15),
                check=False,
                capture_output=True,
                text=True,
            )
            detail = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            for line in detail.splitlines()[-12:]:
                if line.strip():
                    self.log(f"  {line.strip()}")
        except subprocess.TimeoutExpired:
            new_ip = get_public_ip() or old_ip
            self.log("[VPN:proton] Timeout script rotazione")
            return RotateResult(False, old_ip, new_ip, "timeout")
        except Exception as exc:
            return RotateResult(False, old_ip, old_ip, str(exc))

        new_ip = get_public_ip() or old_ip
        # Se lo script ha stampato OK, rileggi
        if "OK IP cambiato" in detail:
            for line in detail.splitlines():
                if "OK IP cambiato:" in line and "->" in line:
                    try:
                        new_ip = line.split("->")[-1].strip()
                    except Exception:
                        pass
        changed = bool(old_ip and new_ip and old_ip != new_ip)
        save_state(
            {
                "provider": "proton",
                "old_ip": old_ip,
                "new_ip": new_ip,
                "ok": changed,
                "ts": time.time(),
            }
        )
        self.log(
            f"[VPN:proton] IP dopo: {new_ip or '?'} changed={'SI' if changed else 'NO'}"
        )
        if not changed:
            self.log(
                "[VPN:proton] Proton Free spesso riprende lo STESSO server. "
                "Disconnect a mano → aspetta 10s → Quick Connect / altro paese, "
                "poi: python mef_vpn.py status"
            )
        return RotateResult(changed, old_ip, new_ip or "", detail[-500:])


def make_vpn_service(
    provider: str = "",
    command: str = "",
    log: LogFn | None = None,
) -> Optional[VpnService]:
    """
    provider: proton | shell | auto
    command: override shell (anche per proton se vuoto usa ps1)
    """
    provider = (provider or os.environ.get("MEF_VPN") or "").strip().lower()
    command = (command or os.environ.get("MEF_VPN_ROTATE_CMD") or "").strip()

    if command and provider in ("", "auto", "shell"):
        return ShellVpnService(command, log=log)
    if provider == "proton" or (provider in ("", "auto") and PROTON_ROTATE_PS1.exists()):
        if command:
            return ShellVpnService(command, log=log)
        return ProtonVpnService(log=log)
    if command:
        return ShellVpnService(command, log=log)
    return None


def is_ip_burned_signal(
    *,
    consecutive_403: int = 0,
    search_failed_after_heal: bool = False,
    local_403_heals: int = 0,
    access_denied_page: bool = False,
) -> bool:
    """
    IP 'bruciato' secondo Akamai — NON un 403 isolato.

    True solo se c'e' evidenza di blocco persistente:
    - pagina Access Denied esplicita, oppure
    - almeno 2 heal locali falliti ancora con 403, oppure
    - ricerca ancora 403 DOPO un heal locale E streak >= 3, oppure
    - streak molto alta (>= 4) anche senza heal.
    """
    if access_denied_page:
        return True
    if local_403_heals >= 2:
        return True
    if search_failed_after_heal and consecutive_403 >= 3:
        return True
    return consecutive_403 >= 4


def variable_download_delay(min_s: float, max_s: float) -> float:
    """
    Delay variabile 'umano' tra PDF:
    distribuzione triangolare centrata un po' sotto la media (piu' naturale dell'uniforme).
    """
    lo = max(3.0, float(min_s))
    hi = max(lo + 0.5, float(max_s))
    mode = lo + (hi - lo) * random.uniform(0.35, 0.55)
    return float(random.triangular(lo, hi, mode))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MEF VPN service")
    parser.add_argument("action", choices=("status", "rotate", "watch-ip"))
    parser.add_argument("--provider", default="proton", help="proton|shell")
    parser.add_argument("--cmd", default="", help="Comando custom (shell)")
    parser.add_argument("--timeout", type=float, default=150.0)
    args = parser.parse_args(argv)

    svc = make_vpn_service(args.provider, args.cmd)
    if svc is None:
        print("Nessun provider VPN. Usa --provider proton oppure --cmd ...")
        return 1

    if args.action == "status":
        st = svc.status()
        print(json.dumps(st, indent=2))
        return 0 if st.get("ip") else 2

    if args.action == "watch-ip":
        prev = ""
        while True:
            ip = get_public_ip()
            if ip != prev:
                print(f"{time.strftime('%H:%M:%S')} IP={ip}")
                prev = ip
            time.sleep(5)

    result = svc.rotate(timeout=args.timeout)
    print(json.dumps({
        "ok": result.ok,
        "ip_changed": result.ip_changed,
        "old_ip": result.old_ip,
        "new_ip": result.new_ip,
        "detail": result.detail[:300],
    }, indent=2))
    return 0 if result.ok or result.ip_changed else 3


if __name__ == "__main__":
    sys.exit(main())
