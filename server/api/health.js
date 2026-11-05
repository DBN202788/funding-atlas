import { route } from "../lib/deps.js";
import { health } from "../lib/handlers.js";

export default route(health, { methods: ["GET"] });
