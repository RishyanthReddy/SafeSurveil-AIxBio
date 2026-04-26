/** @type {import('tailwindcss').Config} */
module.exports = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        surface: "var(--color-surface)",
        "surface-panel": "var(--color-surface-panel)",
        "surface-muted": "var(--color-surface-muted)",
        "surface-strong": "var(--color-surface-strong)",
        ink: "var(--color-ink)",
        "ink-muted": "var(--color-ink-muted)",
        line: "var(--color-line)",
        act: "var(--color-act)",
        review: "var(--color-review)",
        defer: "var(--color-defer)",
      },
      fontFamily: {
        display: ["Space Grotesk", "sans-serif"],
        body: ["Public Sans", "sans-serif"],
        data: ["Space Grotesk", "monospace"],
      },
      spacing: {
        gutter: "16px",
        margin: "32px",
      },
      maxWidth: {
        clinical: "1440px",
      },
      boxShadow: {
        clinical: "0 18px 48px -30px rgba(15, 23, 42, 0.28)",
      },
    },
  },
  plugins: [],
};
