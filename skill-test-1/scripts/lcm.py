#!/usr/bin/env python3
"""计算两个整数的非负最小公倍数；任一整数为零时返回零。"""

import argparse
from math import gcd


def lcm(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return abs((a // gcd(a, b)) * b)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("a", type=int, help="第一个整数")
    parser.add_argument("b", type=int, help="第二个整数")
    args = parser.parse_args()
    print(lcm(args.a, args.b))


if __name__ == "__main__":
    main()
