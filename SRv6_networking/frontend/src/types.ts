export type Json = Record<string, any>;
export interface Device extends Json {id: string; name: string; role: string; running: boolean; state: string; lab_id: string; addresses: {address: string; interface: string; prefixlen: number}[]}
export interface Link extends Json {id: string; source?: string; target?: string; source_name: string; target_name: string; source_interface: string; target_interface: string; status: string; operational?: string}
export interface View {nodes: Device[]; links: Link[]; snapshots: Record<string, Json>; collection_id: string; collected_at: string}
export interface Event extends Json {kind: 'transfer' | 'processing'; description: string; timestamp: number; before: Json; after: Json}
export interface Journey {packet_id: string; direction: string; sequence: number; complete: boolean; issues: string[]; events: Event[]}
export interface Job extends Json {state: string; journeys: Journey[]; errors: string[]}
