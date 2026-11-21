export interface Attachment {
  id: string;
  name: string;
  size: number;
}

export interface ModelOption {
  id: string;
  name: string;
  description?: string;
  badge?: string;
  disabled?: boolean;
}
