/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: "class",
  content: ["./components/ui/**/*.tsx", "./particle/**/*.tsx"],
  corePlugins: { preflight: false }, // Do not reset the existing plain-HTML landing page.
  theme: { extend: {} },
};
