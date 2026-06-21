export interface SessionUser {
  id: number;
  display_name: string;
}

export interface NowPlaying {
  title: string;
  artist?: string | null;
  duration?: number | null;
  thumbnail?: string | null;
  webpage_url?: string | null;
  requested_by?: string | null;
  track_id?: number | null;
}

export interface QueueItem {
  id: number;
  position: number;
  title?: string | null;
  artist?: string | null;
  source_url?: string | null;
  track_id?: number | null;
  added_by?: number | null;
}

export interface QueueState {
  now_playing: NowPlaying | null;
  queue: QueueItem[];
}

export interface Track {
  id: number;
  title: string;
  artist?: string | null;
  duration_seconds?: number | null;
  uploaded_by?: number | null;
}

export interface Playlist {
  id: number;
  name: string;
  created_by?: number | null;
  track_count?: number;
}

export interface PlaylistTrack {
  playlist_track_id: number;
  track_id: number;
  position: number;
  title: string;
  artist?: string | null;
  duration_seconds?: number | null;
}

export interface PlaylistDetail {
  id: number;
  name: string;
  created_by?: number | null;
  tracks: PlaylistTrack[];
}
