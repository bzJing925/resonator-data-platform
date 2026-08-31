/** not-a-knot 三次样条（对齐 scipy.interpolate.CubicSpline 默认 bc_type）。
 *  点数 <4 退化为线性插值（与 Python baseline_interpolate 一致）。 */

function solveDense(a: number[][], b: number[]): number[] {
  // 部分主元高斯消元（样条矩阵对角占优，稳定性足够）
  const n = b.length;
  const m = a.map((row, i) => [...row, b[i]]);
  for (let col = 0; col < n; col++) {
    let piv = col;
    for (let r = col + 1; r < n; r++) {
      if (Math.abs(m[r][col]) > Math.abs(m[piv][col])) piv = r;
    }
    [m[col], m[piv]] = [m[piv], m[col]];
    for (let r = 0; r < n; r++) {
      if (r === col) continue;
      const f = m[r][col] / m[col][col];
      for (let c = col; c <= n; c++) m[r][c] -= f * m[col][c];
    }
  }
  return m.map((row, i) => {
    if (m[i][i] === 0) throw new Error('样条矩阵奇异');
    return row[n] / m[i][i];
  });
}

export function cubicSplineInterpolate(
  xs: number[],
  ys: number[],
  target: number[],
): number[] {
  const n = xs.length;
  if (n < 4) {
    return target.map((t) => {
      if (t <= xs[0]) return ys[0] + ((ys[1] - ys[0]) * (t - xs[0])) / (xs[1] - xs[0]);
      if (t >= xs[n - 1]) {
        return ys[n - 1] + ((ys[n - 1] - ys[n - 2]) * (t - xs[n - 1])) / (xs[n - 1] - xs[n - 2]);
      }
      let i = 0;
      while (xs[i + 1] < t) i++;
      const w = (t - xs[i]) / (xs[i + 1] - xs[i]);
      return ys[i] * (1 - w) + ys[i + 1] * w;
    });
  }
  // Burden 形式：S_i(x) = a_i + b_i·dx + c_i·dx² + d_i·dx³，解 c（=S''/2）
  const h = Array.from({ length: n - 1 }, (_, i) => xs[i + 1] - xs[i]);
  const A: number[][] = Array.from({ length: n }, () => new Array(n).fill(0));
  const rhs = new Array(n).fill(0);
  // not-a-knot 首行：h1·c0 − (h0+h1)·c1 + h0·c2 = 0
  A[0][0] = h[1];
  A[0][1] = -(h[0] + h[1]);
  A[0][2] = h[0];
  // 内部行
  for (let i = 1; i < n - 1; i++) {
    A[i][i - 1] = h[i - 1];
    A[i][i] = 2 * (h[i - 1] + h[i]);
    A[i][i + 1] = h[i];
    rhs[i] = 3 * ((ys[i + 1] - ys[i]) / h[i] - (ys[i] - ys[i - 1]) / h[i - 1]);
  }
  // not-a-knot 末行：d_{n-3}=d_{n-2} → (c_{n-2}−c_{n-3})/h_{n-3} = (c_{n-1}−c_{n-2})/h_{n-2}
  A[n - 1][n - 3] = h[n - 2];
  A[n - 1][n - 2] = -(h[n - 3] + h[n - 2]);
  A[n - 1][n - 1] = h[n - 3];
  const c = solveDense(A, rhs);
  const b = new Array(n - 1);
  const d = new Array(n - 1);
  for (let i = 0; i < n - 1; i++) {
    b[i] = (ys[i + 1] - ys[i]) / h[i] - (h[i] * (2 * c[i] + c[i + 1])) / 3;
    d[i] = (c[i + 1] - c[i]) / (3 * h[i]);
  }
  return target.map((t) => {
    let i = 0;
    if (t <= xs[0]) i = 0;
    else if (t >= xs[n - 1]) i = n - 2;
    else {
      while (xs[i + 1] < t) i++;
    }
    const dx = t - xs[i];
    return ys[i] + b[i] * dx + c[i] * dx * dx + d[i] * dx * dx * dx;
  });
}
