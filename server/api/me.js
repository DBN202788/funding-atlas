import { route } from "../lib/deps.js";
import { me } from "../lib/handlers.js";

export default route(me, { methods: ["GET"] });
