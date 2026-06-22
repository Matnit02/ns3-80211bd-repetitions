#include "bd-phy.h"

#include "../interference-helper.h"

#include "ns3/log.h"
#include "ns3/nstime.h"      // MicroSeconds
#include "ns3/wifi-phy.h"    // AddStaticPhyEntity

NS_LOG_COMPONENT_DEFINE("BdPhy");

namespace ns3
{

BdPhy::BdPhy(OfdmPhyVariant variant)
    : OfdmPhy(variant)
{
}

const PhyEntity::PpduFormats BdPhy::m_bdPpduFormats{
    {WIFI_PREAMBLE_BD,
     {WIFI_PPDU_FIELD_PREAMBLE,      // L-STF + L-LTF (32 µs total at 10 MHz)
      WIFI_PPDU_FIELD_NON_HT_HEADER, // L-SIG (8 µs)
      WIFI_PPDU_FIELD_RL_SIG,        // 8 µs
      WIFI_PPDU_FIELD_NGV_SIG,       // 8 µs
      WIFI_PPDU_FIELD_RNGV_SIG,      // 8 µs
      WIFI_PPDU_FIELD_NGV_STF,       // 8 µs
      WIFI_PPDU_FIELD_NGV_LTF,       // 8 µs
      WIFI_PPDU_FIELD_DATA}}};


const PhyEntity::PpduFormats&
BdPhy::GetPpduFormats() const
{
    return m_bdPpduFormats;
}

Time
BdPhy::GetDuration(WifiPpduField field, const WifiTxVector& txVector) const
{
    switch (field)
    {
    case WIFI_PPDU_FIELD_PREAMBLE: return MicroSeconds(32); // L_STF+L_LTF @10 MHz
    case WIFI_PPDU_FIELD_NON_HT_HEADER: return MicroSeconds(8);   // L_SIG
    case WIFI_PPDU_FIELD_RL_SIG: return MicroSeconds(8);
    case WIFI_PPDU_FIELD_NGV_SIG: return MicroSeconds(8);
    case WIFI_PPDU_FIELD_RNGV_SIG: return MicroSeconds(8);
    case WIFI_PPDU_FIELD_NGV_STF: return MicroSeconds(8);
    case WIFI_PPDU_FIELD_NGV_LTF: return MicroSeconds(8); // SISO, LTF_REP=0
    default: return OfdmPhy::GetDuration(field, txVector); // payload/etc.
    }
}

WifiMode
BdPhy::GetSigMode(WifiPpduField field, const WifiTxVector& txVector) const
{
    switch (field)
    {
    case WIFI_PPDU_FIELD_PREAMBLE:
    case WIFI_PPDU_FIELD_NON_HT_HEADER:
    case WIFI_PPDU_FIELD_RL_SIG:
    case WIFI_PPDU_FIELD_NGV_SIG:
    case WIFI_PPDU_FIELD_RNGV_SIG:
        return GetOfdmRate3MbpsBW10MHz(); // BPSK 1/2 @10 MHz
    case WIFI_PPDU_FIELD_NGV_STF:
    case WIFI_PPDU_FIELD_NGV_LTF:
        return GetHeaderMode(txVector); // training tagged like header
    default:
        return PhyEntity::GetSigMode(field, txVector);
    }
}

PhyEntity::PhyFieldRxStatus
BdPhy::DoEndReceiveField(WifiPpduField field, Ptr<Event> event)
{
    switch (field)
    {
    case WIFI_PPDU_FIELD_NON_HT_HEADER:
    case WIFI_PPDU_FIELD_RL_SIG:
    case WIFI_PPDU_FIELD_NGV_SIG:
    case WIFI_PPDU_FIELD_RNGV_SIG:
    case WIFI_PPDU_FIELD_NGV_STF:
    case WIFI_PPDU_FIELD_NGV_LTF:
        // BD-specific non-data fields are modeled structurally in this custom PHY.
        return PhyFieldRxStatus(true);
    default:
        return OfdmPhy::DoEndReceiveField(field, event);
    }
}

} // namespace ns3

namespace
{
class ConstructorBd
{
  public:
    ConstructorBd()
    {
        // Register BdPhy under its own modulation class so legacy OFDM keeps using OfdmPhy.
        ns3::WifiPhy::AddStaticPhyEntity(ns3::WIFI_MOD_CLASS_BD, ns3::Create<ns3::BdPhy>());
    }
} g_constructor_bd;
} // namespace
