import {
  Building2,
  Car,
  Cloud,
  Cpu,
  Database,
  Globe,
  HardDrive,
  Laptop,
  Monitor,
  Network,
  Radio,
  Router,
  Satellite,
  Server,
  Smartphone,
  type LucideIcon,
} from "lucide-react";
import type { NodeKind } from "../lib/types";

export const NODE_ICONS: Record<string, LucideIcon> = {
  laptop: Laptop,
  desktop: Monitor,
  server: Server,
  phone: Smartphone,
  iot: Cpu,
  cloud: Cloud,
  router: Router,
  switch: Network,
  internet: Globe,
  database: Database,
  radio: Radio,
  satellite: Satellite,
  building: Building2,
  vehicle: Car,
  storage: HardDrive,
};

export function nodeIcon(kind: NodeKind | undefined, icon?: string): LucideIcon {
  if (icon && NODE_ICONS[icon]) return NODE_ICONS[icon];
  switch (kind) {
    case "router":
      return Router;
    case "switch":
      return Network;
    case "external":
      return Globe;
    default:
      return Monitor;
  }
}
