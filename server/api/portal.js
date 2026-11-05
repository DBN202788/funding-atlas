import { route } from "../lib/deps.js";
import { portal } from "../lib/handlers.js";

export default route(portal, { methods: ["POST"] });
