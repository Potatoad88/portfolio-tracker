export const api = async <T>(
  path: string,
  options?: RequestInit,
): Promise<T> => {
  const response = await fetch(path, options);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${response.status})`);
  }
  return response.json();
};

export const money = (value: string | undefined, currency: string) =>
  value == null
    ? "—"
    : new Intl.NumberFormat("en-SG", {
        style: "currency",
        currency,
        maximumFractionDigits: 2,
      }).format(Number(value));
