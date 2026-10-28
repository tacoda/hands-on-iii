import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { Holds } from "./views/Holds";
import { Job } from "./views/Job";
import { Jobs } from "./views/Jobs";
import { Submit } from "./views/Submit";
import "./style.css";

function App() {
  return (
    <BrowserRouter>
      <nav>
        <NavLink to="/" end>Jobs</NavLink>
        <NavLink to="/holds">Holds</NavLink>
        <NavLink to="/submit">Submit</NavLink>
      </nav>
      <main>
        <Routes>
          <Route path="/" element={<Jobs />} />
          <Route path="/jobs/:id" element={<Job />} />
          <Route path="/holds" element={<Holds />} />
          <Route path="/submit" element={<Submit />} />
        </Routes>
      </main>
    </BrowserRouter>
  );
}

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
