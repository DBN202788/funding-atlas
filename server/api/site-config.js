import { route } from "../lib/deps.js";
import { siteConfig } from "../lib/handlers.js";

export default route(siteConfig, { methods: ["GET"] });
