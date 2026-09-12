import "./global.css";
import { setAppIcon } from "./brand";
import { mountApp } from "./AppMount";

setAppIcon();

mountApp(document.getElementById("root")!);
