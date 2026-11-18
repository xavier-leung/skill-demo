#!/usr/bin/env node
'use strict';

// 使用 BigInt 和欧几里得算法，避免大整数的浮点精度损失。
function gcd(a, b) {
  a = a < 0n ? -a : a;
  b = b < 0n ? -b : b;
  while (b !== 0n) {
    [a, b] = [b, a % b];
  }
  return a;
}

function main() {
  const args = process.argv.slice(2);
  if (args.length !== 2 || !args.every((value) => /^[+-]?[0-9]+$/.test(value))) {
    console.error('错误：请提供两个十进制整数。用法：node gcd.js <整数1> <整数2>');
    process.exitCode = 2;
    return;
  }
  console.log(gcd(BigInt(args[0]), BigInt(args[1])).toString());
}

main();
