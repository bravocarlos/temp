#!/usr/bin/env python3
"""
comparar-nodos.py

Compara como quedaron configurados dos nodos en MAAS (discos + red) y
reporta las diferencias campo por campo.

Pensado para verificar que un nodo configurado por FCE con bucketsconfig.yaml
quedo identico a uno configurado a mano desde la GUI.

Necesita maas2bucketsconfig.py en el mismo directorio.

Uso:
    ./comparar-nodos.py --buckets generated/maas/buckets.yaml \\
        --a 4k6ta3 --b 8mfpam

    # con etiquetas legibles
    ./comparar-nodos.py --buckets generated/maas/buckets.yaml \\
        --a 4k6ta3 --etiqueta-a "a mano" \\
        --b 8mfpam --etiqueta-b "por FCE"

Modo offline (para depurar sin tocar MAAS):
    ./comparar-nodos.py --buckets buckets.yaml \\
        --a-json ifaces_a.json,disks_a.json,machine_a.json \\
        --b-json ifaces_b.json,disks_b.json,machine_b.json
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import maas2bucketsconfig as gen
except ImportError:
    sys.exit("Falta maas2bucketsconfig.py en el mismo directorio que este script.")

try:
    import yaml
except ImportError:
    sys.exit("Falta PyYAML:  sudo apt install -y python3-yaml")


# ------------------------------------------------------------------ colores

class C:
    ROJO = "\033[31m"
    VERDE = "\033[32m"
    AMAR = "\033[33m"
    GRIS = "\033[90m"
    NEG = "\033[1m"
    FIN = "\033[0m"

    @classmethod
    def apagar(cls):
        for k in ("ROJO", "VERDE", "AMAR", "GRIS", "NEG", "FIN"):
            setattr(cls, k, "")


# ------------------------------------------------------------------ lectura

def leer_nodo(args, system_id, jsons, snapshot=None):
    """Devuelve (hostname, {'disks': [...], 'nics': [...]}, avisos)."""
    if snapshot:
        ifaces, disks, machine = gen.cargar_snapshot(snapshot)
    elif jsons:
        partes = [p.strip() for p in jsons.split(",")]
        if len(partes) != 3:
            sys.exit("--*-json espera 3 rutas separadas por coma: "
                     "ifaces,disks,machine")
        ifaces = gen.load(partes[0], partes[0])
        disks = gen.load(partes[1], partes[1])
        machine = gen.load(partes[2], partes[2])
    else:
        ifaces = gen.run_maas(args.profile, "interfaces", "read", system_id)
        disks = gen.run_maas(args.profile, "block-devices", "read", system_id)
        machine = gen.run_maas(args.profile, "machine", "read", system_id)

    with open(args.buckets) as fh:
        buckets = yaml.safe_load(fh)

    hostname = machine.get("hostname", system_id or "?")
    _, hw = gen.elegir_bucket(buckets, hostname)
    if hw is None:
        sys.exit("No encontre '%s' en ningun bucket de %s"
                 % (hostname, args.buckets))

    avisos = []
    boot = (machine.get("boot_disk") or {}).get("name")
    datos = {
        "disks": gen.construir_discos(disks, boot, gen.indice_discos(hw), avisos),
        "nics": gen.construir_nics(ifaces, gen.indice_nics(hw), avisos,
                                   solo_usadas=not args.todas_las_nics),
    }
    return hostname, datos, avisos


# --------------------------------------------------------------- comparacion

def por_id(entradas):
    return {e["id"]: e for e in entradas}


def normalizar(valor):
    """Las listas de miembros de bond se comparan como conjuntos ordenados."""
    if isinstance(valor, list):
        return sorted(str(v) for v in valor)
    if isinstance(valor, dict):
        return {k: normalizar(v) for k, v in sorted(valor.items())}
    return valor


def comparar_entrada(a, b):
    """Devuelve [(campo, val_a, val_b), ...] con las diferencias.

    Si el campo es un dict (p.ej. params del bond) baja un nivel y solo
    reporta las subclaves que cambiaron, en vez del dict entero.
    """
    difs = []
    for campo in sorted(set(a) | set(b)):
        va, vb = a.get(campo, "<ausente>"), b.get(campo, "<ausente>")
        if normalizar(va) == normalizar(vb):
            continue
        if isinstance(va, dict) and isinstance(vb, dict):
            for sub in sorted(set(va) | set(vb)):
                sa, sb = va.get(sub, "<ausente>"), vb.get(sub, "<ausente>")
                if normalizar(sa) != normalizar(sb):
                    difs.append(("%s.%s" % (campo, sub), sa, sb))
        else:
            difs.append((campo, va, vb))
    return difs


def comparar_seccion(titulo, lista_a, lista_b, eti_a, eti_b):
    """Imprime el informe de una seccion. Devuelve cuantas difs encontro."""
    a, b = por_id(lista_a), por_id(lista_b)
    todos = list(dict.fromkeys(list(a) + list(b)))
    n_dif = 0

    print("%s=== %s ===%s" % (C.NEG, titulo, C.FIN))

    for ident in todos:
        if ident in a and ident not in b:
            print("  %s--%s  %-22s  solo en %s" % (C.ROJO, C.FIN, ident, eti_a))
            n_dif += 1
        elif ident in b and ident not in a:
            print("  %s++%s  %-22s  solo en %s" % (C.VERDE, C.FIN, ident, eti_b))
            n_dif += 1
        else:
            difs = comparar_entrada(a[ident], b[ident])
            if not difs:
                print("  %sok%s  %-22s" % (C.GRIS, C.FIN, ident))
            else:
                n_dif += len(difs)
                print("  %s!=%s  %-22s" % (C.AMAR, C.FIN, ident))
                for campo, va, vb in difs:
                    print("        %-26s %s%s%s" % (campo, C.ROJO, va, C.FIN))
                    print("        %-26s %s%s%s" % ("", C.VERDE, vb, C.FIN))
    print()
    return n_dif


# ---------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(
        description="Compara la configuracion real de dos nodos en MAAS")
    ap.add_argument("--profile", default="root")
    ap.add_argument("--buckets", required=True,
                    help="ruta a generated/maas/buckets.yaml")
    ap.add_argument("--a", help="system_id del nodo A")
    ap.add_argument("--b", help="system_id del nodo B")
    ap.add_argument("--etiqueta-a", default=None)
    ap.add_argument("--etiqueta-b", default=None)
    ap.add_argument("--a-snapshot", metavar="DIR",
                    help="directorio de snapshot guardado para el lado A")
    ap.add_argument("--b-snapshot", metavar="DIR",
                    help="directorio de snapshot guardado para el lado B")
    ap.add_argument("--a-json", help="ifaces,disks,machine del nodo A")
    ap.add_argument("--b-json", help="ifaces,disks,machine del nodo B")
    ap.add_argument("--todas-las-nics", action="store_true")
    ap.add_argument("--sin-color", action="store_true")
    args = ap.parse_args()

    if args.sin_color or not sys.stdout.isatty():
        C.apagar()

    tiene_a = args.a or args.a_json or args.a_snapshot
    tiene_b = args.b or args.b_json or args.b_snapshot
    if not tiene_a or not tiene_b:
        ap.error("hace falta una fuente por lado: --a/--a-snapshot/--a-json "
                 "y --b/--b-snapshot/--b-json")

    host_a, datos_a, avisos_a = leer_nodo(args, args.a, args.a_json,
                                          args.a_snapshot)
    host_b, datos_b, avisos_b = leer_nodo(args, args.b, args.b_json,
                                          args.b_snapshot)

    eti_a = args.etiqueta_a or (
        "%s (snapshot)" % host_a if args.a_snapshot else host_a)
    eti_b = args.etiqueta_b or (
        "%s (snapshot)" % host_b if args.b_snapshot else host_b)

    print()
    print("%sA:%s %s     %sB:%s %s" % (C.ROJO, C.FIN, eti_a,
                                       C.VERDE, C.FIN, eti_b))
    print()

    total = 0
    total += comparar_seccion("DISCOS", datos_a["disks"], datos_b["disks"],
                              eti_a, eti_b)
    total += comparar_seccion("RED", datos_a["nics"], datos_b["nics"],
                              eti_a, eti_b)

    for a in avisos_a:
        print("  %s* [%s] %s%s" % (C.AMAR, eti_a, a, C.FIN), file=sys.stderr)
    for a in avisos_b:
        print("  %s* [%s] %s%s" % (C.AMAR, eti_b, a, C.FIN), file=sys.stderr)

    if total == 0:
        print("%sIDENTICOS%s - los dos nodos quedaron con la misma "
              "configuracion." % (C.VERDE + C.NEG, C.FIN))
        return 0

    print("%s%d diferencia(s)%s" % (C.AMAR + C.NEG, total, C.FIN))
    return 1


if __name__ == "__main__":
    sys.exit(main())
