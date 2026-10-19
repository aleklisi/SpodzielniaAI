// Runs in the main client bundle before the app (and before any chunk is loaded on demand).
import { installDefaultPolicy } from "@/lib/trusted-types";

installDefaultPolicy(window);
