import { route } from "../lib/deps.js";
import { deleteAccount } from "../lib/handlers.js";

export default route(deleteAccount, { methods: ["POST"] });
