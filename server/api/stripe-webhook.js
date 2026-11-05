import { route } from "../lib/deps.js";
import { stripeWebhook } from "../lib/handlers.js";

export default route(stripeWebhook, { methods: ["POST"], browser: false });
