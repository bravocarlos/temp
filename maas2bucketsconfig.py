#!/usr/bin/env python3
"""
maas2bucketsconfig.py

Lee como esta realmente configurado un nodo en MAAS (discos + red) y genera
el bloque configs: de un common/bucketsconfig.yaml equivalente.

Uso tipico:
    ./maas2bucketsconfig.py --profile root --system-id 4k6ta3 \\
        --buckets generated/maas/buckets.yaml --name cloud

Para depurar sin tocar MAAS, se le pueden pasar los JSON ya volcados:
    maas root interfaces read 4k6ta3   > ifaces.json
    maas root block-devices read 4k6ta3 > disks.json
    maas root machine read 4k6ta3      > machine.json
    ./maas2bucketsconfig.py --ifaces ifaces.json --disks disks.json \\
        --machine machine.json --buckets generated/maas/buckets.yaml
"""

import argparse
import json
import subprocess
import sys

try:
    import yaml
except ImportError:
    sys.exit("Falta PyYAML:  sudo apt install -y python3-yaml")


# ---------------------------------------------------------------- utilidades

def run_maas(profile, *args):
    """Llama al CLI de maas y devuelve el JSON, salteando el preambulo."""
    cmd = ["maas", profile, *args]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        sys.exit("ERROR ejecutando: %s\n%s" % (" ".join(cmd), p.stderr.strip()))
    return parse_json(p.stdout, " ".join(cmd))


def parse_json(text, origen):
    """El CLI de MAAS a veces antepone 'Success.' / 'Machine-readable...'."""
    pos = [text.find(c) for c in "[{"]
    pos = [p for p in pos if p >= 0]
    if not pos:
        sys.exit("No encontre JSON en la salida de: %s" % origen)
    try:
        return json.loads(text[min(pos):])
    except json.JSONDecodeError as e:
        sys.exit("JSON invalido en %s: %s" % (origen, e))


def load(path, origen):
    with open(path) as fh:
        return parse_json(fh.read(), origen)


SNAP = ("ifaces.json", "disks.json", "machine.json")


def guardar_snapshot(directorio, ifaces, disks, machine):
    """Vuelca el estado crudo del nodo para poder compararlo mas adelante."""
    import os
    os.makedirs(directorio, exist_ok=True)
    for nombre, datos in zip(SNAP, (ifaces, disks, machine)):
        with open(os.path.join(directorio, nombre), "w") as fh:
            json.dump(datos, fh, indent=1)
    print("Snapshot guardado en %s/" % directorio.rstrip("/"), file=sys.stderr)


def cargar_snapshot(directorio):
    """Lee un snapshot guardado. Devuelve (ifaces, disks, machine)."""
    import os
    rutas = [os.path.join(directorio, n) for n in SNAP]
    faltan = [r for r in rutas if not os.path.exists(r)]
    if faltan:
        sys.exit("Al snapshot %s le faltan: %s"
                 % (directorio, ", ".join(os.path.basename(f) for f in faltan)))
    return tuple(load(r, r) for r in rutas)


def human_size(nbytes):
    """Convierte bytes al formato corto que usa curtin: 512M, 7T, 440G."""
    for unidad, factor in (("T", 1024 ** 4), ("G", 1024 ** 3),
                           ("M", 1024 ** 2), ("K", 1024)):
        if nbytes >= factor:
            val = nbytes / factor
            if abs(val - round(val)) < 0.01:
                return "%d%s" % (round(val), unidad)
            return "%.2f%s" % (val, unidad)
    return str(nbytes)


# ------------------------------------------------------------------- buckets

def indice_nics(bucket_hw):
    """logicalname -> indice, desde el hardware de un bucket."""
    return {n["logicalname"]: int(i)
            for i, n in (bucket_hw.get("nics") or {}).items()}


def indice_discos(bucket_hw):
    """Lista de (indice, size_str) en el orden del bucket."""
    return sorted(((int(i), d.get("size"), d.get("businfo"))
                   for i, d in (bucket_hw.get("disks") or {}).items()),
                  key=lambda t: t[0])


def elegir_bucket(buckets, hostname):
    """Devuelve el hardware del bucket que contiene a esta maquina."""
    for h, data in buckets.items():
        for m in data.get("machines", []):
            if m.split(".")[0] == hostname.split(".")[0]:
                return h, data["hardware"]
    return None, None


def tam_a_bytes(s):
    """'480G' / '7.68T' -> bytes aproximados, para emparejar discos."""
    if not s:
        return 0
    s = str(s).strip()
    mult = {"T": 1024 ** 4, "G": 1024 ** 3, "M": 1024 ** 2, "K": 1024}
    if s[-1] in mult:
        try:
            return int(float(s[:-1]) * mult[s[-1]])
        except ValueError:
            return 0
    try:
        return int(s)
    except ValueError:
        return 0


# -------------------------------------------------------------------- discos

def construir_discos(block_devices, boot_disk_name, discos_bucket, avisos):
    """Traduce los block devices de MAAS a entradas del bucketsconfig."""
    salida = []

    # Emparejar cada block device con su indice en el bucket, por tamano.
    # Se consume cada indice una sola vez para respetar duplicados.
    disponibles = list(discos_bucket)   # [(idx, size_str, businfo), ...]

    def indice_para(bd):
        objetivo = bd.get("size") or 0
        mejor, mejor_dif = None, None
        for cand in disponibles:
            dif = abs(tam_a_bytes(cand[1]) - objetivo)
            if mejor_dif is None or dif < mejor_dif:
                mejor, mejor_dif = cand, dif
        if mejor is None:
            return None
        # tolerancia del 5% -- los buckets redondean el tamano
        if mejor_dif > objetivo * 0.05:
            avisos.append("disco %s (%s): no encontre un indice claro en el "
                          "bucket, revisalo a mano"
                          % (bd.get("name"), human_size(objetivo)))
        disponibles.remove(mejor)
        return mejor[0]

    # Orden estable: primero el de arranque, despues por nombre
    ordenados = sorted(block_devices,
                       key=lambda d: (d.get("name") != boot_disk_name,
                                      d.get("name", "")))

    for bd in ordenados:
        nombre = bd.get("name")
        idx = indice_para(bd)
        if idx is None:
            avisos.append("disco %s: sin indice, lo salteo" % nombre)
            continue

        entrada = {"id": nombre, "type": "disk", "disk": idx}
        ptable = bd.get("partition_table_type")
        if ptable:
            entrada["ptable"] = ptable.lower()
        if nombre == boot_disk_name:
            entrada["boot"] = True
        salida.append(entrada)

        for part in bd.get("partitions") or []:
            pid = part.get("name") or "%s-part%s" % (nombre, part.get("id"))
            num = None
            # MAAS nombra las particiones <disco>-partN
            if "-part" in pid:
                try:
                    num = int(pid.rsplit("-part", 1)[1])
                except ValueError:
                    num = None
            p = {"id": pid, "type": "partition", "device": nombre,
                 "size": human_size(part.get("size", 0))}
            if num is not None:
                p["number"] = num
            salida.append(p)

            fs = part.get("filesystem") or {}
            if fs.get("fstype"):
                fid = pid + "-format"
                f = {"id": fid, "type": "format",
                     "fstype": fs["fstype"], "volume": pid}
                if fs.get("label"):
                    f["label"] = fs["label"]
                salida.append(f)

                if fs.get("mount_point"):
                    salida.append({"id": pid + "-mount", "type": "mount",
                                   "device": fid,
                                   "path": fs["mount_point"]})

        # disco entero formateado, sin tabla de particiones
        fs = bd.get("filesystem") or {}
        if fs.get("fstype"):
            fid = nombre + "-format"
            f = {"id": fid, "type": "format",
                 "fstype": fs["fstype"], "volume": nombre}
            if fs.get("label"):
                f["label"] = fs["label"]
            salida.append(f)
            if fs.get("mount_point"):
                salida.append({"id": nombre + "-mount", "type": "mount",
                               "device": fid, "path": fs["mount_point"]})

    return salida


# ----------------------------------------------------------------------- red

PARAMS_BOND = ("bond_mode", "bond_lacp_rate", "bond_xmit_hash_policy",
               "bond_miimon", "bond_downdelay", "bond_updelay",
               "bond_num_grat_arp")


def subred_de(iface):
    """Nombres de subred enlazadas, o None si esta sin configurar."""
    nombres = []
    for link in iface.get("links") or []:
        modo = link.get("mode")
        sub = link.get("subnet") or {}
        if modo in ("auto", "static", "dhcp") and sub.get("name"):
            nombres.append(sub["name"])
    return nombres or None


def construir_nics(ifaces, mapa_nics, avisos, solo_usadas=False):
    """Traduce las interfaces de MAAS, en orden: fisicas, bonds, vlans."""
    orden = {"physical": 0, "bond": 1, "bridge": 2, "vlan": 3}
    ordenadas = sorted(ifaces,
                       key=lambda i: (orden.get(i.get("type"), 9),
                                      i.get("name", "")))

    if solo_usadas:
        # fisicas que son miembro de algun bond/bridge
        miembros = set()
        for i in ifaces:
            if i.get("type") in ("bond", "bridge"):
                miembros.update(i.get("parents") or [])
        ordenadas = [i for i in ordenadas
                     if i.get("type") != "physical"
                     or i.get("name") in miembros
                     or subred_de(i)]

    salida = []

    for i in ordenadas:
        nombre = i.get("name")
        tipo = i.get("type")
        vlan = i.get("vlan") or {}

        e = {"id": nombre, "name": nombre, "type": tipo}

        if tipo == "physical":
            if nombre in mapa_nics:
                e["nic"] = mapa_nics[nombre]
            else:
                avisos.append("nic %s: no esta en el bucket (renombrada?), "
                              "completa el campo nic a mano" % nombre)
                e["nic"] = "FIXME"
        elif tipo == "bond":
            e["bond_interfaces"] = list(i.get("parents") or [])
            p = {k: v for k, v in (i.get("params") or {}).items()
                 if k in PARAMS_BOND}
            if p:
                e["params"] = p
        elif tipo == "vlan":
            padres = i.get("parents") or []
            if padres:
                e["vlan_link"] = padres[0]
            else:
                avisos.append("vlan %s: sin padre" % nombre)
        elif tipo == "bridge":
            e["bridge_interfaces"] = list(i.get("parents") or [])

        e["fabric"] = vlan.get("fabric", "default")
        e["vlan_id"] = vlan.get("vid", 0)

        mtu = i.get("effective_mtu") or vlan.get("mtu")
        if mtu:
            e["mtu"] = mtu

        subs = subred_de(i)
        if subs:
            e["subnets"] = subs
        # sin links utiles -> se omite 'subnets' para que quede unconfigured

        salida.append(e)

    return salida


# ---------------------------------------------------------------------- YAML

class Volcador(yaml.SafeDumper):
    """Fuerza flujo compacto en las entradas chicas y evita anclas."""
    def ignore_aliases(self, data):
        return True


def volcar(configs, nombre_alloc, maquina_fqdn):
    doc = {
        "allocations": {nombre_alloc: {"machines": [maquina_fqdn]}},
        "configs": {nombre_alloc: configs},
    }
    return yaml.dump(doc, Dumper=Volcador, default_flow_style=False,
                     sort_keys=False, width=200, allow_unicode=True)


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Genera bucketsconfig.yaml desde un nodo ya configurado "
                    "en MAAS")
    ap.add_argument("--profile", default="root",
                    help="perfil del CLI de maas (default: root)")
    ap.add_argument("--system-id", help="system_id del nodo en MAAS")
    ap.add_argument("--buckets", required=True,
                    help="ruta a generated/maas/buckets.yaml")
    ap.add_argument("--name", default="cloud",
                    help="nombre de la allocation/config (default: cloud)")
    ap.add_argument("--out", help="archivo de salida (default: stdout)")
    ap.add_argument("--todas-las-nics", action="store_true",
                    help="incluir tambien las NICs que no estan en uso "
                         "(por defecto se omiten)")
    ap.add_argument("--guardar-snapshot", metavar="DIR",
                    help="guarda el estado crudo del nodo en DIR para poder "
                         "compararlo despues con comparar-nodos.py")
    # modo offline
    ap.add_argument("--ifaces", help="JSON de 'maas <p> interfaces read <id>'")
    ap.add_argument("--disks", help="JSON de 'maas <p> block-devices read <id>'")
    ap.add_argument("--machine", help="JSON de 'maas <p> machine read <id>'")
    args = ap.parse_args()

    offline = args.ifaces and args.disks and args.machine
    if not offline and not args.system_id:
        ap.error("hace falta --system-id (o los tres JSON para modo offline)")

    if offline:
        ifaces = load(args.ifaces, args.ifaces)
        disks = load(args.disks, args.disks)
        machine = load(args.machine, args.machine)
    else:
        ifaces = run_maas(args.profile, "interfaces", "read", args.system_id)
        disks = run_maas(args.profile, "block-devices", "read", args.system_id)
        machine = run_maas(args.profile, "machine", "read", args.system_id)

    if args.guardar_snapshot:
        guardar_snapshot(args.guardar_snapshot, ifaces, disks, machine)

    with open(args.buckets) as fh:
        buckets = yaml.safe_load(fh)

    hostname = machine.get("hostname", "")
    fqdn = machine.get("fqdn") or hostname
    avisos = []

    hash_bucket, hw = elegir_bucket(buckets, hostname)
    if hw is None:
        sys.exit("No encontre '%s' en ningun bucket de %s"
                 % (hostname, args.buckets))

    boot_disk = (machine.get("boot_disk") or {}).get("name")
    if not boot_disk:
        avisos.append("la maquina no tiene boot_disk definido en MAAS")

    configs = {
        "disks": construir_discos(disks, boot_disk, indice_discos(hw), avisos),
        "nics": construir_nics(ifaces, indice_nics(hw), avisos,
                               solo_usadas=not args.todas_las_nics),
    }

    texto = volcar(configs, args.name, fqdn)

    cabecera = [
        "# Generado desde el nodo %s (%s)" % (hostname, args.system_id or "offline"),
        "# Bucket que lo contiene: %s" % hash_bucket,
        "# Revisa los indices nic:/disk: contra generated/maas/buckets.yaml",
        "# antes de aplicarlo a los demas nodos.",
        "",
    ]
    salida = "\n".join(cabecera) + texto

    if args.out:
        with open(args.out, "w") as fh:
            fh.write(salida)
        print("Escrito en %s" % args.out, file=sys.stderr)
    else:
        print(salida)

    if avisos:
        print("\n--- REVISAR ---", file=sys.stderr)
        for a in avisos:
            print("  * %s" % a, file=sys.stderr)


if __name__ == "__main__":
    main()
