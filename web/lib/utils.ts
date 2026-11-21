/** Join conditional Tailwind utility classes without a runtime dependency. */
export function cn(...classes: Array<string | undefined | null | false>) {
  return classes.filter(Boolean).join(" ");
}
