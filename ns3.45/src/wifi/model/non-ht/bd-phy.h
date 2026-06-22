#ifndef BD_PHY_H
#define BD_PHY_H

#include "ofdm-phy.h"

namespace ns3
{

class BdPhy : public OfdmPhy
{
  public:
    explicit BdPhy(OfdmPhyVariant variant = OFDM_PHY_10_MHZ);
    const PpduFormats& GetPpduFormats() const override;
    Time GetDuration(WifiPpduField field, const WifiTxVector& txVector) const override;
    WifiMode GetSigMode(WifiPpduField field, const WifiTxVector& txVector) const override;

  protected:
    PhyFieldRxStatus DoEndReceiveField(WifiPpduField field, Ptr<Event> event) override;

  private:
    static const PpduFormats m_bdPpduFormats;
};

} // namespace ns3

#endif // BD_PHY_H
