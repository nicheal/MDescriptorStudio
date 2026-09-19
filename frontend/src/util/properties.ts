/** Display names for the frame properties the health pass tracks.

The rail's missing-values subtitle and the findings drawer both tag these, and a
second copy means one of them quietly stops translating a new property.
*/
export const DATASET_PROPERTY_LABELS: Record<string, string> = {
  energy: "Energy",
  forces: "Forces",
  virial: "Virial",
};
