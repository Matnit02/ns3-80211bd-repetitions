#include "bd-ppdu.h"

namespace ns3
{

BdPpdu::BdPpdu(Ptr<const WifiPsdu> psdu,
               const WifiTxVector& txVector,
               const WifiPhyOperatingChannel& channel,
               uint64_t uid)
    : OfdmPpdu(psdu, txVector, channel, uid)
{
    // Nothing extra: bd preamble/type is already in txVector; BdPhy sets timings.
}

Ptr<WifiPpdu>
BdPpdu::Copy() const
{
    return Ptr<WifiPpdu>(new BdPpdu(*this), false);
}

} // namespace ns3
