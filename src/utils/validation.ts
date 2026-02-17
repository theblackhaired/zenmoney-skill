const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_REGEX = /^\d{4}-\d{2}-\d{2}$/;
const MONTH_REGEX = /^\d{4}-\d{2}$/;

export function validateUUID(value: string, field: string): void {
  if (!UUID_REGEX.test(value)) {
    throw new Error(`Invalid UUID for ${field}: ${value}`);
  }
}

export function validateDate(value: string, field: string): void {
  if (!DATE_REGEX.test(value)) {
    throw new Error(`Invalid date format for ${field}: ${value}. Expected yyyy-MM-dd`);
  }
  const [y, m, d] = value.split('-').map(Number);
  const date = new Date(y, m - 1, d);
  if (date.getFullYear() !== y || date.getMonth() !== m - 1 || date.getDate() !== d) {
    throw new Error(`Invalid date for ${field}: ${value}`);
  }
}

export function validateMonth(value: string, field: string): void {
  if (!MONTH_REGEX.test(value)) {
    throw new Error(`Invalid month format for ${field}: ${value}. Expected yyyy-MM`);
  }
}

export function validatePositiveNumber(value: number, field: string): void {
  if (typeof value !== 'number' || value < 0 || isNaN(value)) {
    throw new Error(`${field} must be a non-negative number, got: ${value}`);
  }
}

export function todayString(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
