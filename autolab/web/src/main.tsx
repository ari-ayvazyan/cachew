import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App"

// follow the OS light/dark setting
const mq = matchMedia("(prefers-color-scheme: dark)")
const applyTheme = () => document.documentElement.classList.toggle("dark", mq.matches)
applyTheme()
mq.addEventListener("change", applyTheme)

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>)
