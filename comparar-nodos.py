#!/usr/bin/env python3
"""
resumen-netplan.py

Resume un netplan o un curtin config a una linea por interfaz, en orden
estable, para poder diffear dos nodos sin leer 2000 lineas de YAML.

Uso:
    ./resumen-netplan.py /tmp/curtin-ok.yaml
    diff -u <(./resumen-netplan.py /tmp/curtin-ok.yaml) \\
            <(./resumen-netplan.py /tmp/curtin-fce.yaml)

Acepta:
  - netplan puro (/etc/netplan/50-cloud-init.yaml)
  - curtin config con network: v2 anidado
  - curtin config v1 (network: {config: [...]})
"""

import sys

try:
    import yaml
except ImportError:
    sys.exit("Falta PyYAML:  sudo apt install -y python3-yaml")

SECCIONES = ("ethernets", "bonds", "vlans", "bridges")


def sacar_v2(doc):
    """Encuentra el bloque netplan v2 este donde este."""
    if not isinstance(doc, dict):
        return None
    if any(s in doc for s in SECCIONES):
        return doc
    net = doc.get("network")
    if isinstance(net, dict):
        if any(s in net for s in SECCIONES):
            return net
        return sacar_v2(net)
    # curtin a veces mete el netplan dentro de write_files / debconf
    for v in doc.values():
        hallado = sacar_v2(v) if isinstance(v, dict) else None
        if hallado:
            return hallado
    return None


def resumir_rutas(cfg):
    partes = []
    for r in cfg.get("routes") or []:
        tabla = r.get("table", "main")
        partes.append("%s>%s@t%s" % (r.get("to", "?"), r.get("via", "?"), tabla))
    return partes


def resumir_politica(cfg):
    partes = []
    for p in cfg.get("routing-policy") or []:
        partes.append("from=%s>t%s" % (p.get("from", "?"), p.get("table", "?")))
    return partes


def linea(seccion, nombre, cfg):
    campos = ["%-8s" % seccion, "%-16s" % nombre]

    campos.append("mtu=%s" % cfg.get("mtu", "-"))

    if cfg.get("dhcp4"):
        campos.append("dhcp4=true")

    dirs = cfg.get("addresses") or []
    campos.append("ip=%s" % (",".join(str(d) for d in dirs) if dirs else "-"))

    gw = cfg.get("gateway4") or cfg.get("gateway6")
    campos.append("GW=%s" % (gw if gw else "-"))

    rutas = resumir_rutas(cfg)
    campos.append("rutas=%s" % (";".join(rutas) if rutas else "-"))

    pol = resumir_politica(cfg)
    campos.append("politica=%s" % (";".join(pol) if pol else "-"))

    if cfg.get("optional"):
        campos.append("optional=true")

    for k in ("link", "id", "interfaces"):
        if k in cfg:
            v = cfg[k]
            campos.append("%s=%s" % (k, ",".join(v) if isinstance(v, list) else v))

    ns = (cfg.get("nameservers") or {}).get("addresses")
    if ns:
        campos.append("dns=%s" % ",".join(ns))

    return "  ".join(campos)


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)

    with open(sys.argv[1]) as fh:
        docs = [d for d in yaml.safe_load_all(fh) if d]

    v2 = None
    for d in docs:
        v2 = sacar_v2(d)
        if v2:
            break

    if not v2:
        sys.exit("No encontre un bloque netplan v2 en %s" % sys.argv[1])

    salida = []
    for seccion in SECCIONES:
        for nombre, cfg in sorted((v2.get(seccion) or {}).items()):
            salida.append(linea(seccion, nombre, cfg or {}))

    print("\n".join(salida))

    con_gw = [l for l in salida if " GW=" in l and "GW=-" not in l]
    print()
    if con_gw:
        print("--- default gateway en: %s"
              % ", ".join(l.split()[1] for l in con_gw))
    else:
        print("--- NINGUNA interfaz tiene gateway4; todo va por policy routing")


if __name__ == "__main__":
    sys.exit(main())
