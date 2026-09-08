"""Operator-only admission limit. Run in the deployment's worker container."""
import argparse
import json

from .db import connect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--capacity', type=int, choices=range(2, 33), metavar='2..32')
    args = parser.parse_args()
    with connect() as conn:
        row = conn.execute('SELECT capacity FROM execution_pool WHERE id=1 FOR UPDATE').fetchone()
        used = conn.execute('SELECT COALESCE(sum(slots),0) AS n FROM execution_reservations').fetchone()['n']
        if args.capacity is not None:
            if args.capacity < used:
                parser.error('Wait for active reservations to drain before reducing capacity')
            conn.execute('UPDATE execution_pool SET capacity=%s WHERE id=1', (args.capacity,))
            row['capacity'] = args.capacity
        print(json.dumps({**row, 'reserved': used}))


if __name__ == '__main__':
    main()
