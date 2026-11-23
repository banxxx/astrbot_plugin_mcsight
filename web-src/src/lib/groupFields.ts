/* 群别名与展示群号的输入规则：与后端 official_core.alias_text/display_group_text 保持一致。 */

export function charUnits(char: string): number {
  const code = char.codePointAt(0) ?? 0;
  return (code >= 0x1100 && code <= 0x115f) || (code >= 0x2e80 && code <= 0xa4cf)
    || (code >= 0xac00 && code <= 0xd7a3) || (code >= 0xf900 && code <= 0xfaff)
    || (code >= 0xfe30 && code <= 0xfe6f) || (code >= 0xff00 && code <= 0xff60)
    || (code >= 0xffe0 && code <= 0xffe6) ? 2 : 1;
}

export function aliasUnits(value: string): number {
  return Array.from(value).reduce((total, char) => total + charUnits(char), 0);
}

/** 最多 8 个中文字符或 16 个英文字符（按显示宽度裁剪）。 */
export function clipAlias(value: string, limit = 16): string {
  let result = "";
  let units = 0;
  for (const char of Array.from(value)) {
    const next = charUnits(char);
    if (units + next > limit) break;
    result += char;
    units += next;
  }
  return result;
}

/** 展示群号只保留 ASCII 数字。 */
export function clipDigits(value: string, limit = 20): string {
  return (value ?? "").replace(/[^0-9]/g, "").slice(0, limit);
}
