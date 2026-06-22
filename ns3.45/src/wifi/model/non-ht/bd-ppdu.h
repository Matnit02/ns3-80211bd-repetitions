#ifndef BD_PPDU_H
#define BD_PPDU_H

#include "ofdm-ppdu.h"

namespace ns3
{
/**
 * PPDU for 802.11bd (NGV) modeled as OFDM with bd preamble/SIG fields.
 * Reuses OFDM payload handling; bd header/preamble timing comes from BdPhy.
 */
class BdPpdu : public OfdmPpdu
{
  public:
    BdPpdu(Ptr<const WifiPsdu> psdu,
           const WifiTxVector& txVector,
           const WifiPhyOperatingChannel& channel,
           uint64_t uid = 0);
    Ptr<WifiPpdu> Copy() const override;
};

} // namespace ns3

#endif // BD_PPDU_H
