#!/usr/bin/env python3
"""
diff-crudo.py

Diffea el JSON COMPLETO que devuelve la API de MAAS para dos nodos, sin
filtrar campos. A diferencia de comparar-nodos.py, que solo mira lo que
entra en el bucketsconfig, esto compara todo: enabled, tags, children,
vlan.dhcp_on, numa_node, sriov, lo que sea.

Si comparar-nodos.py dice IDENTICOS y el comportamiento es distinto,
la diferencia esta en algun campo que este script SI va a mostrar.

Solo quita claves volatiles (ids internos, URIs, timestamps) porque
cambian en cada creacion y tapan todo lo demas.

Uso:
    ./diff-crudo.py --a-snapshot /srv/snap-oscan01-a-mano --b 4k6ta3
    ./diff-crudo.py --a 4k6ta3 --b 8mfpam --ignorar-macs
    ./diff-crudo.py --a-snapshot DIR --b ID --que machine
"""

import argparse
import difflib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import maas2bucketsconfig as gen
except ImportError:
    sys.exit("Falta maas2bucketsconfig.py en el mismo directorio.")


# Claves que cambian en cada creacion y no dicen nada sobre la config.
VOLATILES = {
    "id", "resource_uri", "href", "system_id", "created", "updated",
    "node_id", "interface_id", "link_id", "uuid", "path",
    "discovered", "firmware_version", "product", "serial", "model",
}


def limpiar(obj, ignorar_macs=False):
    """Saca claves volatiles de forma recursiva."""
    if isinstance(obj, dict):
        salida = {}
        for k, v in obj.items():
            if k in VOLATILES:
                continue
            if ignorar_macs and k in ("mac_address", "mac_addresses",
                                      "parent_mac", "boot_interface_mac"):
                continue
            salida[k] = limpiar(v, ignorar_macs)
        return salida
    if isinstance(obj, list):
        return [limpiar(v, ignorar_macs) for v in obj]
    return obj


def ordenar(obj):
    """Ordena listas de interfaces/discos por nombre para que el diff alinee."""
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        clave = None
        for c in ("name", "id_path", "model"):
            if c in obj[0]:
                clave = c
                break
        if clave:
            return sorted((ordenar(o) for o in obj),
                          key=lambda d: str(d.get(clave, "")))
        return [ordenar(o) for o in obj]
    if isinstance(obj, dict):
        return {k: ordenar(v) for k, v in obj.items()}
    return obj


def leer(args, system_id, snapshot, que):
    if snapshot:
        ifaces, disks, machine = gen.cargar_snapshot(snapshot)
    else:
        ifaces = gen.run_maas(args.profile, "interfaces", "read", system_id)
        disks = gen.run_maas(args.profile, "block-devices", "read", system_id)
        machine = gen.run_maas(args.profile, "machine", "read", system_id)
    return {"interfaces": ifaces, "disks": disks, "machine": machine}[que]


def texto(obj, ignorar_macs):
    obj = ordenar(limpiar(obj, ignorar_macs))
    return json.dumps(obj, indent=2, sort_keys=True,
                      ensure_ascii=False).splitlines(keepends=True)


def main():
    ap = argparse.ArgumentParser(
        description="Diff del JSON crudo de MAAS entre dos nodos")
    ap.add_argument("--profile", default="root")
    ap.add_argument("--a")
    ap.add_argument("--b")
    ap.add_argument("--a-snapshot", metavar="DIR")
    ap.add_argument("--b-snapshot", metavar="DIR")
    ap.add_argument("--etiqueta-a", default="A")
    ap.add_argument("--etiqueta-b", default="B")
    ap.add_argument("--que", default="interfaces",
                    choices=["interfaces", "disks", "machine"],
                    help="que objeto comparar (default: interfaces)")
    ap.add_argument("--ignorar-macs", action="store_true",
                    help="util comparando nodos DISTINTOS, donde las MAC "
                         "difieren siempre")
    ap.add_argument("--contexto", type=int, default=3)
    args = ap.parse_args()

    if not (args.a or args.a_snapshot) or not (args.b or args.b_snapshot):
        ap.error("hace falta una fuente por lado")

    a = leer(args, args.a, args.a_snapshot, args.que)
    b = leer(args, args.b, args.b_snapshot, args.que)

    ta = texto(a, args.ignorar_macs)
    tb = texto(b, args.ignorar_macs)

    d = list(difflib.unified_diff(ta, tb,
                                  fromfile="%s (%s)" % (args.etiqueta_a, args.que),
                                  tofile="%s (%s)" % (args.etiqueta_b, args.que),
                                  n=args.contexto))
    if not d:
        print("Sin diferencias en '%s' (quitando ids/URIs/timestamps)."
              % args.que)
        return 0

    sys.stdout.writelines(d)
    print()
    print("%d lineas de diff" % sum(1 for l in d
                                    if l[0] in "+-" and l[1] not in "+-"))
    return 1


if __name__ == "__main__":
    sys.exit(main())
