export interface AssetImpactPreview {
  staleArtifactIds: string[];
  untouchedArtifactIds: string[];
}

export const hasDownstreamImpact = (preview: AssetImpactPreview) => preview.staleArtifactIds.length > 1;
