import forms from "@tailwindcss/forms";

/** @type {import('tailwindcss').Config} */
// The same brand as the public site (../../../tailwind.config.js): navy #11355C, orange #F57921.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { 50: "#f2f6fa", 100: "#e3eaf2", 600: "#1b4d7e", 700: "#16416a", 800: "#133b61", 900: "#11355c", 950: "#0c2743" },
        accent: { 50: "#fef5ec", 100: "#fde4cf", 200: "#fbc9a3", 400: "#f89550", 500: "#f57921", 600: "#dc6712", 700: "#b6530f" },
      },
      fontFamily: {
        sans: ['"Plus Jakarta Sans"', "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Arial", "sans-serif"],
      },
    },
  },
  plugins: [forms],
};
