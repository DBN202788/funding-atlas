import { route } from "../lib/deps.js";
import { advisor } from "../lib/handlers.js";

export const config = { maxDuration: 300 };
export default route(advisor, { methods: ["POST"] });
