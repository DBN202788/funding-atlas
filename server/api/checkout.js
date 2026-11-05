import { route } from "../lib/deps.js";
import { checkout } from "../lib/handlers.js";

export default route(checkout, { methods: ["POST"] });
