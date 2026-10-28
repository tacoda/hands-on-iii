import { defineConfig, loadEnv, type Plugin } from "vite";
import react from "@vitejs/plugin-react";

// In development the page talks to the http worker directly, with no Caddy in
// front. The factory still checks the password, so the proxy adds it from
// DEV_AUTH=operator:<password> in web/.env.local, which git ignores.
// Because the proxy adds the password to every request, it also copies
// Caddy's 415 rule: a form from another site can't send JSON.
const jsonPostsOnly: Plugin = {
  name: "json-posts-only",
  configureServer(server) {
    server.middlewares.use("/api", (req, res, next) => {
      if (req.method === "POST" && !(req.headers["content-type"] ?? "").startsWith("application/json")) {
        res.statusCode = 415;
        res.end();
      } else next();
    });
  },
};

export default defineConfig(({ mode }) => {
  const { DEV_AUTH } = loadEnv(mode, process.cwd(), "DEV_");
  return {
    plugins: [react(), jsonPostsOnly],
    server: {
      proxy: {
        "/api": {
          target: "http://127.0.0.1:3111",
          ...(DEV_AUTH ? { auth: DEV_AUTH } : {}),
        },
      },
    },
  };
});
