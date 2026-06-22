#include "ns3/core-module.h"
#include "ns3/network-module.h"
#include "ns3/mobility-module.h"
#include "ns3/wifi-module.h"
#include "ns3/txop.h"
#include "ns3/log.h"
#include "ns3/simulator.h"
#include "ns3/random-variable-stream.h"
#include "ns3/tag.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <cstddef>
#include <utility>
#include <vector>

using namespace ns3;
namespace fs = std::filesystem;

NS_LOG_COMPONENT_DEFINE("Wifi11pHighway3p3");

static std::vector<std::string> g_laneOf;
static std::vector<Ptr<Node>> g_nodes;
static double g_roadLengthForDist = 0.0;
static double g_binWidth = 50.0;
static double g_maxRange = 1500.0;
static uint32_t g_nBins = 0;
static std::vector<uint64_t> g_opportunities;
static std::vector<uint64_t> g_successes;
static std::string g_outCsv = "prr_vs_distance.csv";

static std::string g_csvDir = "results_csv";
static double g_cbrIntervalSec = 0.1;
static int32_t g_cbrNodeId = 0;
static std::string g_cbrOutCsv = "cbr.csv";
static std::vector<std::pair<double, double>> g_cbrMeasurements;

static uint64_t g_totalTx = 0;
static uint64_t g_totalRx = 0;
static bool g_ldpcGainEnabled = false;

struct RxTxSeqKey
{
    uint32_t rxId;
    uint32_t txId;
    uint32_t seq;

    bool operator==(const RxTxSeqKey& o) const noexcept
    {
        return rxId == o.rxId && txId == o.txId && seq == o.seq;
    }
};

struct RxTxSeqKeyHash
{
    std::size_t operator()(const RxTxSeqKey& k) const noexcept
    {
        std::size_t h = 1469598103934665603ull;
        auto mix = [&](uint32_t v) {
            h ^= static_cast<std::size_t>(v);
            h *= 1099511628211ull;
        };
        mix(k.rxId);
        mix(k.txId);
        mix(k.seq);
        return h;
    }
};

static std::unordered_set<RxTxSeqKey, RxTxSeqKeyHash> g_seen;
static std::unordered_map<RxTxSeqKey, double, RxTxSeqKeyHash> g_mrcSnrLinear;

struct PendingInfo {
    Vector txPos{0.0, 0.0, 0.0};
    Time txTime{Seconds(0)};
    uint64_t dataRate{0};
};
static std::unordered_map<RxTxSeqKey, PendingInfo, RxTxSeqKeyHash> g_pendingInfo;

static Ptr<UniformRandomVariable> g_u01 = CreateObject<UniformRandomVariable>();


static inline uint32_t BinIndex(double d)
{
    if (d < 0.0 || d > g_maxRange) return UINT32_MAX;
    uint32_t idx = static_cast<uint32_t>(d / g_binWidth);
    if (idx >= g_nBins) idx = g_nBins - 1;
    return idx;
}

static inline double ToroidalDistance(const Vector& a, const Vector& b, double L)
{
    double dx = std::fabs(a.x - b.x);
    if (L > 0.0) {
        dx = std::fmod(dx, L);
        if (dx > L * 0.5) dx = L - dx;
    }
    const double dy = a.y - b.y;
    return std::sqrt(dx * dx + dy * dy);
}

static inline double DbToLin(double db) { return std::pow(10.0, db / 10.0); }
static inline double LinToDb(double lin) { return 10.0 * std::log10(std::max(lin, 1e-12)); }

static Time ComputeAutoTxopLimit(Ptr<WifiNetDevice> dev, uint32_t pktSizeBytes,
                                 uint32_t retransmissions, std::string dataMode)
{
    if (retransmissions == 0) {
        return Seconds(0);
    }

    Ptr<WifiPhy> phy = dev->GetPhy();
    const Time sifs = phy->GetSifs();
    const WifiPhyBand band = phy->GetPhyBand();
    const WifiMode mode(dataMode);

    WifiTxVector txv;

    txv.SetPreambleType(GetPreambleForTransmission(mode.GetModulationClass()));
    txv.SetChannelWidth(phy->GetChannelWidth());
    txv.SetGuardInterval(Seconds(1.6e-6));
    txv.SetNss(1);
    txv.SetMode(mode);

    const Time oneFrame = phy->CalculateTxDuration(pktSizeBytes, txv, band, 0);
    const uint32_t attempts = 1u + retransmissions;
    Time burst = attempts * oneFrame + (attempts - 1) * sifs;


    burst += burst * 0.20;


    const uint64_t quantum_us = 32;
    const double burst_us_f = burst.GetSeconds() * 1e6;
    uint64_t burst_us = static_cast<uint64_t>(std::ceil(burst_us_f));
    burst_us = ((burst_us + quantum_us - 1) / quantum_us) * quantum_us;

    return MicroSeconds(burst_us);
}

static std::unordered_set<uint32_t> ParseNodeIdList(const std::string& csv)
{
    std::unordered_set<uint32_t> ids;
    if (csv.empty()) return ids;

    std::stringstream ss(csv);
    std::string token;
    while (std::getline(ss, token, ',')) {

        token.erase(0, token.find_first_not_of(" \t\r\n"));
        if (!token.empty()) {
            token.erase(token.find_last_not_of(" \t\r\n") + 1);
        }
        if (token.empty()) continue;

        char* endp = nullptr;
        long v = std::strtol(token.c_str(), &endp, 10);
        if (endp == token.c_str() || *endp != '\0' || v < 0) {
            std::cout << "PCAP: skipping invalid node id token: \"" << token << "\"\n";
            continue;
        }
        ids.insert(static_cast<uint32_t>(v));
    }
    return ids;
}


class TxInfoTag : public Tag {
public:
    TxInfoTag() = default;
    TxInfoTag(uint32_t senderId, const Vector& pos, double txTimeSec, uint32_t seq)
        : m_senderId(senderId), m_txX(pos.x), m_txY(pos.y), m_txTime(txTimeSec), m_seq(seq) {}

    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::TxInfoTag").SetParent<Tag>().AddConstructor<TxInfoTag>();
        return tid;
    }
    TypeId GetInstanceTypeId() const override { return GetTypeId(); }
    uint32_t GetSerializedSize() const override { return 4 + 8 + 8 + 8 + 4; }

    void Serialize(TagBuffer i) const override
    {
        i.WriteU32(m_senderId);
        i.WriteDouble(m_txX);
        i.WriteDouble(m_txY);
        i.WriteDouble(m_txTime);
        i.WriteU32(m_seq);
    }

    void Deserialize(TagBuffer i) override
    {
        m_senderId = i.ReadU32();
        m_txX = i.ReadDouble();
        m_txY = i.ReadDouble();
        m_txTime = i.ReadDouble();
        m_seq = i.ReadU32();
    }

    void Print(std::ostream& os) const override
    {
        os << "sender=" << m_senderId << " tx=(" << m_txX << "," << m_txY << ")"
           << " t=" << m_txTime << "s seq=" << m_seq;
    }

    Vector GetTxPos() const { return Vector(m_txX, m_txY, 0.0); }
    double GetTxTime() const { return m_txTime; }
    uint32_t GetSenderId() const { return m_senderId; }
    uint32_t GetSeq() const { return m_seq; }

private:
    uint32_t m_senderId{0};
    double m_txX{0.0}, m_txY{0.0};
    double m_txTime{0.0};
    uint32_t m_seq{0};
};


class VeinsBdErrorRateModel : public ErrorRateModel {
public:
    static TypeId GetTypeId()
    {
        static TypeId tid = TypeId("ns3::VeinsBdErrorRateModel")
                                .SetParent<ErrorRateModel>()
                                .SetGroupName("Wifi")
                                .AddConstructor<VeinsBdErrorRateModel>();
        return tid;
    }

    VeinsBdErrorRateModel() = default;

    double DoGetChunkSuccessRate(WifiMode mode, const WifiTxVector& txVector, double snrLinear, uint64_t nBits, uint8_t nTx,
                                 WifiPpduField field, uint16_t channelFreqMhz) const override
    {
        if (field != WIFI_PPDU_FIELD_DATA) return 1.0;
        uint64_t dataRate = mode.GetDataRate(txVector.GetChannelWidth());
        return GetSuccessRateForDataRate(dataRate, snrLinear, nBits);
    }

    static bool HasLdpcGainFormula(uint64_t dataRate)
    {
        switch (dataRate)
        {
            case 6000000:
            case 12000000:
            case 18000000:
            case 24000000:
            case 27000000:
                return true;
            default:
                return false;
        }
    }

    static double ApplyLdpcGain(uint64_t dataRate, double snrLinear)
    {
        if (!g_ldpcGainEnabled)
        {
            return snrLinear;
        }

        if (!HasLdpcGainFormula(dataRate))
        {
            NS_FATAL_ERROR("Unsupported data rate for LDPC gain model: "
                           << dataRate
                           << " bps. Add gain formula or disable --ldpcGainEnabled.");
        }

        const double snrDb = LinToDb(snrLinear);
        double gain = 1.0;

        switch (dataRate) {
            case 6000000:
                gain = std::min(2.5380, std::pow(63.5825, (snrDb - 4.7147) / 9.8621));
                break;
            case 12000000:
                gain = std::min(3.2068, std::pow(46.2607, (snrDb - 9.8316) / 12.7195));
                break;
            case 18000000:
                gain = std::min(3.3370, std::pow(31.8567, (snrDb - 12.3492) / 13.1420));
                break;
            case 24000000:
                gain = std::min(3.1257, std::pow(13.5496, (snrDb - 20.2006) / 4.6429));
                break;
            case 27000000:
                gain = std::min(5.4807, std::pow(5.2883, (snrDb - 16.5610) / 7.0980));
                break;
            default:
                NS_FATAL_ERROR("Internal error: missing LDPC gain formula branch for data rate "
                               << dataRate << " bps");
        }

        if (!std::isfinite(gain) || gain < 1.0) {
            gain = 1.0;
        }
        return snrLinear * gain;
    }

    static double GetSuccessRateForDataRate(uint64_t dataRate, double snrLinear, uint32_t nBits)
    {
        const double effectiveSnr = ApplyLdpcGain(dataRate, snrLinear);


        if (dataRate == 3000000) return getFecBpskBer(effectiveSnr, nBits, 1);
        if (dataRate == 4500000) return getFecBpskBer(effectiveSnr, nBits, 3);
        if (dataRate == 6000000) return getFecQpskBer(effectiveSnr, nBits, 1);
        if (dataRate == 9000000) return getFecQpskBer(effectiveSnr, nBits, 3);
        if (dataRate == 12000000) return getFec16QamBer(effectiveSnr, nBits, 1);
        if (dataRate == 18000000) return getFec16QamBer(effectiveSnr, nBits, 3);
        if (dataRate == 24000000) return getFec64QamBer(effectiveSnr, nBits, 2);
        if (dataRate == 27000000) return getFec64QamBer(effectiveSnr, nBits, 3);


        if (dataRate == 32500000) return getFec64QamBer(effectiveSnr, nBits, 5);
        if (dataRate == 39000000) return getFec256QamBer(effectiveSnr, nBits, 3);

        return getFecQpskBer(effectiveSnr, nBits, 1);
    }

    static double getBpskBer(double snr) { return 0.5 * std::erfc(std::sqrt(snr)); }
    static double getQpskBer(double snr) { return 0.5 * std::erfc(std::sqrt(snr / 2.0)); }
    static double get16QamBer(double snr) { return 0.375 * std::erfc(std::sqrt(snr / 10.0)); }
    static double get64QamBer(double snr) { return (7.0 / 12.0) * 0.5 * std::erfc(std::sqrt(snr / 42.0)); }
    static double get256QamBer(double snr) { return (15.0 / 64.0) * std::erfc(std::sqrt(snr / 170.0)); }

    static double calculatePe(double p, uint32_t bValue)
    {
        double D = std::sqrt(4.0 * p * (1.0 - p));
        double pe = 1.0;

        if (bValue == 1) {
            pe = 0.5 * (36.0 * std::pow(D, 10) + 211.0 * std::pow(D, 12) + 1404.0 * std::pow(D, 14) + 11633.0 * std::pow(D, 16) + 77433.0 * std::pow(D, 18) + 502690.0 * std::pow(D, 20) + 3322763.0 * std::pow(D, 22) + 21292910.0 * std::pow(D, 24) + 134365911.0 * std::pow(D, 26));
        } else if (bValue == 2) {
            pe = 1.0 / (2.0 * bValue) * (3.0 * std::pow(D, 6) + 70.0 * std::pow(D, 7) + 285.0 * std::pow(D, 8) + 1276.0 * std::pow(D, 9) + 6160.0 * std::pow(D, 10) + 27128.0 * std::pow(D, 11) + 117019.0 * std::pow(D, 12) + 498860.0 * std::pow(D, 13) + 2103891.0 * std::pow(D, 14) + 8784123.0 * std::pow(D, 15));
        } else if (bValue == 3) {
            pe = 1.0 / (2.0 * bValue) * (42.0 * std::pow(D, 5) + 201.0 * std::pow(D, 6) + 1492.0 * std::pow(D, 7) + 10469.0 * std::pow(D, 8) + 62935.0 * std::pow(D, 9) + 379644.0 * std::pow(D, 10) + 2253373.0 * std::pow(D, 11) + 13073811.0 * std::pow(D, 12) + 75152755.0 * std::pow(D, 13) + 428005675.0 * std::pow(D, 14));
        } else if (bValue == 5) {
            pe = 1.0 / (2.0 * bValue) * (92.0 * std::pow(D, 4) + 528.0 * std::pow(D, 5) + 8694.0 * std::pow(D, 6) + 79453.0 * std::pow(D, 7) + 792114.0 * std::pow(D, 8) + 7375573.0 * std::pow(D, 9) + 67884974.0 * std::pow(D, 10) + 610875423.0 * std::pow(D, 11) + 5427275376.0 * std::pow(D, 12) + 47664215639.0 * std::pow(D, 13));
        }
        return pe;
    }

    static double getFecBpskBer(double snr, uint32_t nbits, uint32_t bValue)
    {
        double ber = getBpskBer(snr);
        if (ber == 0.0) return 1.0;
        double pe = std::min(calculatePe(ber, bValue), 1.0);
        return std::pow(1.0 - pe, (double)nbits);
    }
    static double getFecQpskBer(double snr, uint32_t nbits, uint32_t bValue)
    {
        double ber = getQpskBer(snr);
        if (ber == 0.0) return 1.0;
        double pe = std::min(calculatePe(ber, bValue), 1.0);
        return std::pow(1.0 - pe, (double)nbits);
    }
    static double getFec16QamBer(double snr, uint32_t nbits, uint32_t bValue)
    {
        double ber = get16QamBer(snr);
        if (ber == 0.0) return 1.0;
        double pe = std::min(calculatePe(ber, bValue), 1.0);
        return std::pow(1.0 - pe, (double)nbits);
    }
    static double getFec64QamBer(double snr, uint32_t nbits, uint32_t bValue)
    {
        double ber = get64QamBer(snr);
        if (ber == 0.0) return 1.0;
        double pe = std::min(calculatePe(ber, bValue), 1.0);
        return std::pow(1.0 - pe, (double)nbits);
    }
    static double getFec256QamBer(double snr, uint32_t nbits, uint32_t bValue)
    {
        double ber = get256QamBer(snr);
        if (ber == 0.0) return 1.0;
        double pe = std::min(calculatePe(ber, bValue), 1.0);
        return std::pow(1.0 - pe, (double)nbits);
    }
};

NS_OBJECT_ENSURE_REGISTERED(VeinsBdErrorRateModel);

static Ptr<WifiNetDevice> g_cbrDevice = nullptr;
static Time g_lastCbrMeasurementTime = Seconds(0);
static Time g_accumulatedBusyTime = Seconds(0);
static Time g_cbrWarmupEnd = Seconds(0);

static void PhyStateCallback(Time start, Time duration, WifiPhyState state)
{
    if (start + duration <= g_cbrWarmupEnd) {
        return;
    }

    if (state == WifiPhyState::TX || state == WifiPhyState::RX || state == WifiPhyState::CCA_BUSY) {

        Time effectiveStart = std::max(start, g_cbrWarmupEnd);
        Time effectiveDuration = (start + duration) - effectiveStart;
        g_accumulatedBusyTime += effectiveDuration;
    }
}

static void MeasureCbr()
{
    if (!g_cbrDevice) return;

    Time now = Simulator::Now();
    Time intervalDuration = now - g_lastCbrMeasurementTime;

    double cbr = 0.0;
    if (intervalDuration.GetSeconds() > 0.0) {
        cbr = g_accumulatedBusyTime.GetSeconds() / intervalDuration.GetSeconds();
    }
    cbr = std::min(1.0, std::max(0.0, cbr));

    g_cbrMeasurements.push_back({now.GetSeconds(), cbr});
    g_lastCbrMeasurementTime = now;

    g_accumulatedBusyTime = Seconds(0);

    Simulator::Schedule(Seconds(g_cbrIntervalSec), &MeasureCbr);
}


static void OnMonitorSnifferRx(double preambleDbm, uint32_t rxId, Ptr<const Packet> pkt,
                               uint16_t, WifiTxVector txVector, MpduInfo, SignalNoiseDbm sn, uint16_t)
{
    TxInfoTag t;
    if (!pkt->PeekPacketTag(t)) return;

    RxTxSeqKey key{rxId, t.GetSenderId(), t.GetSeq()};

    if (g_seen.find(key) != g_seen.end()) return;
    if (sn.signal < preambleDbm) return;

    const double snrDb = sn.signal - sn.noise;
    const double snrLin = DbToLin(snrDb);
    g_mrcSnrLinear[key] += snrLin;

    uint64_t rate = txVector.GetMode().GetDataRate(txVector.GetChannelWidth());
    g_pendingInfo[key] = PendingInfo{t.GetTxPos(), Seconds(t.GetTxTime()), rate};
}

static bool ReceiveL2(Ptr<NetDevice> dev, Ptr<const Packet> pkt, uint16_t,
                      const Address&, const Address&, NetDevice::PacketType)
{
    g_totalRx++;
    TxInfoTag t;
    if (pkt->PeekPacketTag(t)) {
        Ptr<Node> rxNode = dev->GetNode();
        const uint32_t rxId = rxNode->GetId();
        RxTxSeqKey key{rxId, t.GetSenderId(), t.GetSeq()};

        if (g_seen.find(key) != g_seen.end()) {
            return true;
        }

        auto rxCv = rxNode->GetObject<ConstantVelocityMobilityModel>();
        if (rxCv) {
            const double dt = std::max(0.0, (Simulator::Now() - Seconds(t.GetTxTime())).GetSeconds());
            const Vector rxNow = rxCv->GetPosition();
            const Vector v = rxCv->GetVelocity();
            const Vector rxAtTx(rxNow.x - v.x * dt, rxNow.y - v.y * dt, rxNow.z - v.z * dt);

            const double d = ToroidalDistance(t.GetTxPos(), rxAtTx, g_roadLengthForDist);
            const uint32_t b = BinIndex(d);
            if (b != UINT32_MAX) {
                g_successes[b] += 1;
                g_seen.insert(key);
                auto it = g_mrcSnrLinear.find(key);
                if (it != g_mrcSnrLinear.end()) g_mrcSnrLinear.erase(it);
                auto itP = g_pendingInfo.find(key);
                if (itP != g_pendingInfo.end()) g_pendingInfo.erase(itP);
            }
        }
    }
    return true;
}

class BroadcastApp : public Application {
public:
    void Setup(Ptr<WifiNetDevice> dev, Time interval, uint32_t sizeBytes, Time jitter, uint32_t retransmissions)
    {
        m_dev = dev;
        m_interval = interval;
        m_size = sizeBytes;
        m_jitter = jitter;
        m_retx = retransmissions;
        m_jitterRv = CreateObject<UniformRandomVariable>();
    }

private:
    void StartApplication() override
    {
        m_running = true;
        m_dev->SetPromiscReceiveCallback(MakeCallback(&ReceiveL2));

        double phaseSec = 0.0;
        if (m_interval.GetSeconds() > 0.0) {
            phaseSec = m_jitterRv->GetValue(0.0, m_interval.GetSeconds());
        }
        m_event = Simulator::Schedule(Seconds(phaseSec), &BroadcastApp::SendBurst, this);
    }

    void StopApplication() override
    {
        m_running = false;
        if (m_event.IsPending()) {
            Simulator::Cancel(m_event);
        }
    }

    void SendBurst()
    {
        if (!m_running) return;

        Ptr<Node> txNode = m_dev->GetNode();
        auto txCv = txNode->GetObject<ConstantVelocityMobilityModel>();

        const uint32_t attempts = 1u + m_retx;
        const uint32_t seq = m_nextSeq++;
        for (uint32_t a = 0; a < attempts; ++a) {
            const Vector txPos = txCv ? txCv->GetPosition() : Vector();

            if (a == 0) {
                for (auto& n : g_nodes) {
                    if (n == txNode) continue;
                    auto cv = n->GetObject<ConstantVelocityMobilityModel>();
                    if (!cv) continue;
                    const double d = ToroidalDistance(txPos, cv->GetPosition(), g_roadLengthForDist);
                    const uint32_t b = BinIndex(d);
                    if (b != UINT32_MAX) g_opportunities[b] += 1;
                }
            }

            Ptr<Packet> p = Create<Packet>(m_size);
            p->AddPacketTag(TxInfoTag(txNode->GetId(), txPos, Simulator::Now().GetSeconds(), seq));
            m_dev->Send(p, Mac48Address::GetBroadcast(), 0x0800);
            g_totalTx++;
        }

        double j = 0.0;
        if (m_jitter.GetSeconds() > 0.0) {
            j = m_jitterRv->GetValue(-m_jitter.GetSeconds(), m_jitter.GetSeconds());
        }

        Time nextGap = m_interval + Seconds(j);
        const Time kMinGap = MilliSeconds(1);
        if (nextGap < kMinGap) nextGap = kMinGap;

        m_event = Simulator::Schedule(nextGap, &BroadcastApp::SendBurst, this);
    }

    Ptr<WifiNetDevice> m_dev;
    EventId m_event;
    Time m_interval;
    Time m_jitter{Seconds(0)};
    Ptr<UniformRandomVariable> m_jitterRv;
    bool m_running{false};
    uint32_t m_size{0};
    uint32_t m_retx{0};
    uint32_t m_nextSeq{0};
};

static uint32_t VehiclesFromDensityTotal(double vehPerKmTotal, double roadLenMeters)
{
    if (vehPerKmTotal <= 0.0 || roadLenMeters <= 0.0) return 0;
    const double expected = vehPerKmTotal * (roadLenMeters / 1000.0);
    return std::max(1u, static_cast<uint32_t>(std::floor(expected + 0.5)));
}

int main(int argc, char** argv)
{
    double roadLength = 2000.0;
    const double laneWidth = 4.0;
    double density = 5.0;
    double speedMeanKmh = 120.0;
    double speedStdKmh = 12.0;
    double pktIntervalSec = 0.1;
    uint32_t pktSizeBytes = 350;
    double pktJitterSec = 0.01;
    double appStartBase = 1.0;
    double simStop = 120.0;
    bool logPerVehicleLanes = false;
    uint32_t retransmissions = 0;
    double txopLimitSec = 0.0;
    double preambleDbm = -100.0;
    bool ldpcGainEnabled = g_ldpcGainEnabled;
    std::string dataMode = "OfdmRate6MbpsBW10MHz";
    std::string pcapNodeIds = "";
    std::string pcapPrefix = "v2v";
    static std::string g_pcapDir = "results_pcap";

    double binWidthCli = g_binWidth;
    double maxRangeCli = g_maxRange;
    std::string outCsvCli = g_outCsv;
    std::string csvDirCli = g_csvDir;
    static std::string g_graphDir = "results_graph";
    double cbrIntervalCli = g_cbrIntervalSec;

    CommandLine cmd(__FILE__);
    cmd.AddValue("roadLength", "Highway length in meters", roadLength);
    cmd.AddValue("density", "Vehicle density (veh/km)", density);
    cmd.AddValue("speedMeanKmh", "Average vehicle speed in km/h", speedMeanKmh);
    cmd.AddValue("speedStdKmh", "Vehicle speed std dev in km/h", speedStdKmh);
    cmd.AddValue("pktInterval", "Broadcast interval in seconds", pktIntervalSec);
    cmd.AddValue("pktJitter", "Uniform jitter amplitude (+/-) in seconds", pktJitterSec);
    cmd.AddValue("pktSize", "Broadcast payload size in bytes", pktSizeBytes);
    cmd.AddValue("simStop", "Simulation stop time in seconds", simStop);
    cmd.AddValue("logPerVehicleLanes", "Print one line per node with its lane label", logPerVehicleLanes);
    cmd.AddValue("binWidth", "Distance bin width in meters", binWidthCli);
    cmd.AddValue("maxRange", "Max TX-RX distance to consider", maxRangeCli);
    cmd.AddValue("outCsv", "Output CSV filename", outCsvCli);
    cmd.AddValue("csvDir", "Directory for CSV outputs", csvDirCli);
    cmd.AddValue("graphDir", "Directory for PRR plots", g_graphDir);
    cmd.AddValue("cbrInterval", "CBR measurement interval in seconds (default 0.1)", cbrIntervalCli);
    cmd.AddValue("cbrNodeId", "Node ID for CBR measurement (-1 = disabled)", g_cbrNodeId);
    cmd.AddValue("cbrOutCsv", "CBR output CSV filename", g_cbrOutCsv);
    cmd.AddValue("retransmissions", "Extra repeats per periodic message", retransmissions);
    cmd.AddValue("txopLimit", "TXOP limit per burst (seconds)", txopLimitSec);
    cmd.AddValue("preambleDbm", "RxSensitivity in dBm", preambleDbm);
    cmd.AddValue("ldpcGainEnabled", "Enable LDPC gain model (applies to all frames in this scenario)", ldpcGainEnabled);
    cmd.AddValue("dataMode", "Wifi Mode", dataMode);
    cmd.AddValue("pcapNodeIds", "Enable PCAP for a comma-separated list of node ids (e.g. \"0,3,7\"; empty disables)", pcapNodeIds);
    cmd.AddValue("pcapPrefix", "PCAP file prefix", pcapPrefix);
    cmd.AddValue("pcapDir", "Directory for PCAP outputs", g_pcapDir);
    cmd.Parse(argc, argv);

    g_ldpcGainEnabled = ldpcGainEnabled;

    {
        const WifiMode selectedMode(dataMode);
        const uint64_t selectedRate = selectedMode.GetDataRate(10);
        if (g_ldpcGainEnabled && !VeinsBdErrorRateModel::HasLdpcGainFormula(selectedRate))
        {
            NS_FATAL_ERROR("Unsupported dataMode for --ldpcGainEnabled=true: "
                           << dataMode
                           << " (" << selectedRate << " bps at 10 MHz). "
                           << "Add gain formula or disable ldpcGainEnabled.");
        }
    }

    std::cout << "Preamble detection threshold: " << preambleDbm << " dBm\n";
    std::cout << "LDPC gain model: " << (g_ldpcGainEnabled ? "enabled" : "disabled") << "\n";
    std::cout << "Using Wifi Mode: " << dataMode << "\n";

    g_roadLengthForDist = roadLength;
    g_binWidth = std::max(0.1, binWidthCli);
    g_maxRange = std::max(g_binWidth, maxRangeCli);
    g_nBins = static_cast<uint32_t>(std::floor(g_maxRange / g_binWidth)) + 1;
    g_opportunities.assign(g_nBins, 0);
    g_successes.assign(g_nBins, 0);
    g_csvDir = csvDirCli;
    g_cbrIntervalSec = std::max(0.001, cbrIntervalCli);
    g_ldpcGainEnabled = ldpcGainEnabled;
    fs::create_directories(g_csvDir);
    fs::create_directories(g_graphDir);
    fs::create_directories(g_pcapDir);
    g_outCsv = (fs::path(g_csvDir) / fs::path(outCsvCli).filename()).string();

    Time::SetResolution(Time::NS);

    NodeContainer allNodes;
    const uint32_t nTotal = VehiclesFromDensityTotal(density, roadLength);
    const uint32_t nLanesPerDir = 3;
    const uint32_t nUp = nTotal / 2 + (nTotal % 2);
    const uint32_t nDn = nTotal / 2;

    auto splitAcrossLanes = [](uint32_t n, uint32_t lanes) {
        std::vector<uint32_t> perLane(lanes, n / lanes);
        for (uint32_t r = 0; r < (n % lanes); ++r) perLane[r] += 1;
        return perLane;
    };
    const auto upCounts = splitAcrossLanes(nUp, nLanesPerDir);
    const auto dnCounts = splitAcrossLanes(nDn, nLanesPerDir);

    std::vector<NodeContainer> lanesUp(nLanesPerDir), lanesDown(nLanesPerDir);
    auto createLane = [&](uint32_t count, NodeContainer& lane) {
        for (uint32_t k = 0; k < count; ++k) {
            Ptr<Node> n = CreateObject<Node>();
            allNodes.Add(n);
            lane.Add(n);
        }
    };

    for (uint32_t i = 0; i < nLanesPerDir; ++i) {
        createLane(upCounts[i], lanesUp[i]);
        createLane(dnCounts[i], lanesDown[i]);
    }

    g_laneOf.resize(allNodes.GetN());
    if (allNodes.GetN() == 0) {
        std::cout << "No vehicles created. Exiting." << std::endl;
        return 0;
    }

    g_nodes.clear();
    g_nodes.reserve(allNodes.GetN());
    for (uint32_t i = 0; i < allNodes.GetN(); ++i) {
        g_nodes.push_back(allNodes.Get(i));
    }


    {
        const size_t N = allNodes.GetN();

        const double vehPerM = static_cast<double>(N) / std::max(roadLength, 1.0);

        size_t neighborsInRange = static_cast<size_t>(std::ceil(2.0 * g_maxRange * vehPerM));
        if (N > 0) {
            neighborsInRange = std::min(neighborsInRange, N - 1);
        }

        size_t approxKeys = N * std::max<size_t>(neighborsInRange, 16);
        approxKeys *= 4;

        g_seen.max_load_factor(0.7f);
        g_mrcSnrLinear.max_load_factor(0.7f);
        g_pendingInfo.max_load_factor(0.7f);

        g_seen.reserve(approxKeys);
        g_mrcSnrLinear.reserve(approxKeys);
        g_pendingInfo.reserve(approxKeys);
    }

    YansWifiChannelHelper chan;
    chan.SetPropagationDelay("ns3::ConstantSpeedPropagationDelayModel");

    const double fspl1m_59 = 47.86;
    const double dBp = 4.0 * 1.5 * 1.5 / (299792458.0 / 5.9e9);

    chan.AddPropagationLoss("ns3::ThreeLogDistancePropagationLossModel",
                            "ReferenceLoss", DoubleValue(fspl1m_59),
                            "Distance0", DoubleValue(1.0),
                            "Distance1", DoubleValue(dBp),
                            "Distance2", DoubleValue(450.0),
                            "Exponent0", DoubleValue(2.0),
                            "Exponent1", DoubleValue(3.0),
                            "Exponent2", DoubleValue(5.4));

    chan.AddPropagationLoss("ns3::CorrelatedShadowingPropagationLossModel",
                            "CorrelationDistance", DoubleValue(40.0));

    chan.AddPropagationLoss("ns3::NakagamiPropagationLossModel",
                            "m0", DoubleValue(3.0),
                            "m1", DoubleValue(3.0),
                            "m2", DoubleValue(1.0),
                            "Distance1", DoubleValue(dBp),
                            "Distance2", DoubleValue(450.0));

    YansWifiPhyHelper phy;
    phy.SetChannel(chan.Create());
    phy.Set("TxPowerStart", DoubleValue(23.0));
    phy.Set("TxPowerEnd", DoubleValue(23.0));
    phy.Set("TxGain", DoubleValue(3.0));
    phy.Set("TxPowerLevels", UintegerValue(1));
    phy.Set("RxNoiseFigure", DoubleValue(6.0));
    phy.Set("ChannelSettings", StringValue("{178, 10, BAND_5GHZ, 0}"));
    phy.Set("CcaEdThreshold", DoubleValue(-65.0));
    phy.SetErrorRateModel("ns3::VeinsBdErrorRateModel");

    WifiHelper wifi;
    wifi.SetStandard(WIFI_STANDARD_80211bd);
    wifi.SetRemoteStationManager("ns3::ConstantRateWifiManager",
                                 "DataMode", StringValue(dataMode),
                                 "DataMode", StringValue(dataMode),
                                 "ControlMode", StringValue(dataMode));
    Config::SetDefault("ns3::WifiRemoteStationManager::NonUnicastMode", StringValue(dataMode));

    WifiMacHelper mac;
    mac.SetType("ns3::AdhocWifiMac");

    NetDeviceContainer devices = wifi.Install(phy, mac, allNodes);

    if (!pcapNodeIds.empty()) {
        const auto ids = ParseNodeIdList(pcapNodeIds);
        for (uint32_t nodeId : ids) {
            if (nodeId >= allNodes.GetN()) {
                std::cout << "PCAP not enabled: nodeId=" << nodeId
                          << " is out of range (0.." << (allNodes.GetN() ? allNodes.GetN() - 1 : 0) << ")\n";
                continue;
            }

            Ptr<NetDevice> nd = allNodes.Get(nodeId)->GetDevice(0);
            Ptr<WifiNetDevice> wdev = DynamicCast<WifiNetDevice>(nd);
            if (!wdev) {
                std::cout << "PCAP not enabled: nodeId=" << nodeId << " has no WifiNetDevice at device(0)\n";
                continue;
            }

            const std::string perNodePrefix =
                (fs::path(g_pcapDir) / (pcapPrefix + "-n" + std::to_string(nodeId))).string();
            phy.EnablePcap(perNodePrefix, wdev, true);
            std::cout << "PCAP enabled for node " << nodeId << " -> prefix: " << perNodePrefix << "\n";
        }
    }


    for (uint32_t i = 0; i < devices.GetN(); ++i) {
        auto dev = DynamicCast<WifiNetDevice>(devices.Get(i));
        auto wphy = dev->GetPhy();

        PointerValue pv;
        wphy->GetAttribute("PreambleDetectionModel", pv);
        Ptr<ThresholdPreambleDetectionModel> tpd = DynamicCast<ThresholdPreambleDetectionModel>(pv.Get<Object>());

        if (!tpd) {
            tpd = CreateObject<ThresholdPreambleDetectionModel>();
            wphy->SetAttribute("PreambleDetectionModel", PointerValue(tpd));
        }

        tpd->SetAttribute("MinimumRssi", DoubleValue(-85.0));
        wphy->SetAttribute("RxSensitivity", DoubleValue(preambleDbm));

        const uint32_t rxId = dev->GetNode()->GetId();
        wphy->TraceConnectWithoutContext("MonitorSnifferRx", MakeBoundCallback(&OnMonitorSnifferRx, preambleDbm, rxId));

        if (g_cbrNodeId >= 0 && rxId == static_cast<uint32_t>(g_cbrNodeId)) {
            Ptr<WifiPhyStateHelper> stateHelper = wphy->GetState();
            if (stateHelper) {
                stateHelper->TraceConnectWithoutContext("State", MakeCallback(&PhyStateCallback));
                g_cbrDevice = dev;
            }
        }
    }

    if (g_cbrNodeId >= 0 && g_cbrDevice) {
        g_cbrWarmupEnd = Seconds(appStartBase);
        g_lastCbrMeasurementTime = Seconds(appStartBase);
        g_accumulatedBusyTime = Seconds(0);
        Simulator::Schedule(Seconds(appStartBase + g_cbrIntervalSec), &MeasureCbr);
        std::cout << "CBR measurement enabled for node " << g_cbrNodeId
                  << " with interval " << g_cbrIntervalSec * 1000.0 << " ms"
                  << " (warmup ends at " << appStartBase << " s)\n";
    } else if (g_cbrNodeId >= 0) {
        std::cout << "Warning: CBR node " << g_cbrNodeId << " not found or invalid\n";
    }

    Time txopLimit = Seconds(txopLimitSec);

    if (txopLimit == Seconds(0.0) && retransmissions > 0) {
        Ptr<WifiNetDevice> refDev = StaticCast<WifiNetDevice>(devices.Get(0));
        txopLimit = ComputeAutoTxopLimit(refDev, pktSizeBytes, retransmissions, dataMode);
        std::cout << "Auto TXOP limit = " << txopLimit.GetSeconds() * 1e3
                  << " ms (reps=" << retransmissions << ", pkt=" << pktSizeBytes
                  << " B, mode=" << dataMode << ")\n";
    }

    MobilityHelper mobility;
    mobility.SetMobilityModel("ns3::ConstantVelocityMobilityModel");
    mobility.Install(allNodes);

    const std::vector<uint8_t> aifsn = {6};
    const std::vector<uint32_t> cw = {15};
    for (uint32_t i = 0; i < devices.GetN(); ++i) {
        Ptr<WifiNetDevice> dev = DynamicCast<WifiNetDevice>(devices.Get(i));
        Ptr<AdhocWifiMac> m = DynamicCast<AdhocWifiMac>(dev->GetMac());

        Ptr<Txop> txop = m->GetTxop();
        if (!txop) {
            txop = m->GetQosTxop(AC_BE);
        }

        NS_ABORT_MSG_IF(!txop, "No best-effort Txop/QosTxop found for device " << i);

        txop->SetAifsns(aifsn);
        txop->SetMaxCws(cw);

        if (txopLimit > Seconds(0.0)) {
            txop->SetTxopLimit(txopLimit);
        }
    }

    Ptr<NormalRandomVariable> speedRv = CreateObject<NormalRandomVariable>();
    speedRv->SetAttribute("Mean", DoubleValue(speedMeanKmh));
    speedRv->SetAttribute("Variance", DoubleValue(speedStdKmh * speedStdKmh));
    speedRv->SetAttribute("Bound", DoubleValue(3.0 * speedStdKmh));

    Ptr<UniformRandomVariable> xRv = CreateObject<UniformRandomVariable>();
    xRv->SetAttribute("Min", DoubleValue(0.0));
    xRv->SetAttribute("Max", DoubleValue(roadLength));

    auto assignLane = [&](const NodeContainer& laneNodes, bool positiveX, uint32_t laneIdx) {
        const uint32_t nVeh = laneNodes.GetN();
        if (nVeh == 0) return;

        const double signY = positiveX ? +1.0 : -1.0;
        const double y = signY * ((laneIdx + 0.5) * laneWidth);

        for (uint32_t k = 0; k < nVeh; ++k) {
            Ptr<Node> node = laneNodes.Get(k);
            Ptr<ConstantVelocityMobilityModel> cv = node->GetObject<ConstantVelocityMobilityModel>();

            const std::string label = std::string(positiveX ? "UP" : "DOWN") + " lane " + std::to_string(laneIdx);
            g_laneOf[node->GetId()] = label;

            const double x = xRv->GetValue();
            cv->SetPosition(Vector(x, y, 1.5));

            double speedKmh = speedRv->GetValue();
            const double speedMps = speedKmh / 3.6;
            const double vx = positiveX ? speedMps : -speedMps;
            cv->SetVelocity(Vector(vx, 0.0, 0.0));
        }
    };

    auto armExactWrap = [](Ptr<ConstantVelocityMobilityModel> cv, double L, auto&& armRef) -> void {
        if (!cv) return;
        Vector p = cv->GetPosition();
        Vector v = cv->GetVelocity();
        const double vx = v.x;
        if (std::abs(vx) < 1e-9) return;

        double tToEdge = (vx > 0.0) ? (L - p.x) / vx : (0.0 - p.x) / vx;
        if (tToEdge < 1e-6) tToEdge = 1e-6;

        Simulator::Schedule(Seconds(tToEdge), [cv, L, &armRef]() {
            Vector pNow = cv->GetPosition();
            Vector vel = cv->GetVelocity();
            double x = std::fmod(pNow.x, L);
            if (x < 0.0) x += L;
            if (x >= L) x -= L;
            cv->SetPosition(Vector(x, pNow.y, pNow.z));
            cv->SetVelocity(vel);
            armRef(cv, L, armRef);
        });
    };

    for (uint32_t i = 0; i < 3; ++i) {
        assignLane(lanesUp[i], true, i);
        assignLane(lanesDown[i], false, i);
    }

    uint32_t upSum = 0, dnSum = 0;
    std::cout << "Lane population summary:\n";
    for (uint32_t i = 0; i < nLanesPerDir; ++i) {
        upSum += lanesUp[i].GetN();
        dnSum += lanesDown[i].GetN();
        std::cout << "  UP   lane " << i << ": " << lanesUp[i].GetN() << "\n";
        std::cout << "  DOWN lane " << i << ": " << lanesDown[i].GetN() << "\n";
    }
    std::cout << "Direction totals: UP=" << upSum << ", DOWN=" << dnSum
              << " (TOTAL=" << allNodes.GetN() << ")\n";

    if (logPerVehicleLanes) {
        std::cout << "Per-vehicle lane map:\n";
        for (uint32_t i = 0; i < allNodes.GetN(); ++i) {
            std::cout << "  Node " << i << " -> " << g_laneOf[i] << "\n";
        }
    }
    std::cout.flush();

    for (uint32_t i = 0; i < allNodes.GetN(); ++i) {
        Ptr<ConstantVelocityMobilityModel> cv = allNodes.Get(i)->GetObject<ConstantVelocityMobilityModel>();
        armExactWrap(cv, roadLength, armExactWrap);
    }

    const Time pktInterval = Seconds(pktIntervalSec);
    const Time pktJitter = Seconds(pktJitterSec);

    for (uint32_t i = 0; i < allNodes.GetN(); ++i) {
        Ptr<BroadcastApp> app = CreateObject<BroadcastApp>();
        app->Setup(StaticCast<WifiNetDevice>(devices.Get(i)), pktInterval, pktSizeBytes, pktJitter, retransmissions);
        allNodes.Get(i)->AddApplication(app);
        app->SetStartTime(Seconds(appStartBase));
        app->SetStopTime(Seconds(simStop - appStartBase));
    }

    Simulator::Stop(Seconds(simStop));
    Simulator::Run();

    {
        uint64_t rescued = 0;
        for (const auto& kv : g_mrcSnrLinear) {
            const RxTxSeqKey& key = kv.first;
            if (g_seen.find(key) != g_seen.end()) continue;

            auto itP = g_pendingInfo.find(key);
            if (itP == g_pendingInfo.end()) continue;

            const double snrLinear = kv.second;
            const auto& info = itP->second;
            uint32_t nBits = (pktSizeBytes + 34) * 8;

            double psucc = VeinsBdErrorRateModel::GetSuccessRateForDataRate(
                info.dataRate,
                snrLinear,
                nBits);

            if (key.rxId < g_nodes.size()) {
                Ptr<Node> rxNode = g_nodes[key.rxId];
                auto rxCv = rxNode->GetObject<ConstantVelocityMobilityModel>();
                if (rxCv) {
                    const double dt = std::max(0.0, (Simulator::Now() - info.txTime).GetSeconds());
                    const Vector rxNow = rxCv->GetPosition();
                    const Vector v = rxCv->GetVelocity();
                    const Vector rxAtTx(rxNow.x - v.x * dt, rxNow.y - v.y * dt, rxNow.z - v.z * dt);
                    const double d = ToroidalDistance(info.txPos, rxAtTx, g_roadLengthForDist);
                    const uint32_t b = BinIndex(d);
                    if (b != UINT32_MAX) {
                        if (g_u01->GetValue(0.0, 1.0) <= psucc) {
                            g_successes[b] += 1;
                            g_seen.insert(key);
                            rescued++;
                        }
                    }
                }
            }
        }
        std::cout << "MRC rescue added successes: " << rescued << std::endl;
        g_mrcSnrLinear.clear();
        g_pendingInfo.clear();
    }
    Simulator::Destroy();

    std::cout << "Total packets sent:     " << g_totalTx << std::endl;
    std::cout << "Total packets received: " << g_totalRx << std::endl;

    uint64_t totalOpp = 0;
    uint64_t totalSuc = 0;

    {
        std::ofstream out(g_outCsv.c_str());
        out << "distance;prr;successes;opportunities\n";

        auto toComma = [](double x) {
            std::ostringstream oss;
            oss << std::fixed << std::setprecision(6) << x;
            std::string s = oss.str();
            std::replace(s.begin(), s.end(), '.', ',');
            return s;
        };

        for (uint32_t b = 0; b < g_nBins; ++b) {
            const uint64_t opp = g_opportunities[b];
            const uint64_t suc = g_successes[b];
            const double prr = (opp > 0) ? double(suc) / double(opp) : 0.0;
            const double dUpper = (b + 1) * g_binWidth;

            out << toComma(dUpper) << ";" << toComma(prr) << ";" << suc << ";" << opp << "\n";

            totalOpp += opp;
            totalSuc += suc;
        }
        out.close();
        std::cout << "Wrote PRR table to: " << g_outCsv << std::endl;
    }

    const double overallPrr = (totalOpp > 0) ? double(totalSuc) / double(totalOpp) : 0.0;

    std::cout << "\n==== PRR Summary ====\n";
    std::cout << "Opportunities: " << totalOpp << "\n";
    std::cout << "Successes:     " << totalSuc << "\n";
    std::cout << std::fixed << std::setprecision(4) << "Overall PRR:   " << overallPrr << "\n\n";

    std::cout << std::left << std::setw(16) << "Dist<= (m)" << std::setw(14) << "PRR"
              << std::setw(14) << "Successes" << std::setw(14) << "Opportunities" << "\n";

    for (uint32_t b = 0; b < g_nBins; ++b) {
        const uint64_t opp = g_opportunities[b];
        if (opp == 0) continue;

        const uint64_t suc = g_successes[b];
        const double prr = double(suc) / double(opp);
        const double dUpper = (b + 1) * g_binWidth;

        std::cout << std::left << std::setw(16) << std::lround(dUpper)
                  << std::setw(14) << std::setprecision(4) << prr
                  << std::setw(14) << suc
                  << std::setw(14) << opp << "\n";
    }
    std::cout << "===============================\n\n";

    if (!g_cbrMeasurements.empty()) {
        const fs::path cbrCsvPath = fs::path(g_csvDir) / fs::path(g_cbrOutCsv).filename();

        std::ofstream cbrOut(cbrCsvPath.string().c_str());
        cbrOut << "time_s;cbr\n";

        auto toComma = [](double x) {
            std::ostringstream oss;
            oss << std::fixed << std::setprecision(6) << x;
            std::string s = oss.str();
            std::replace(s.begin(), s.end(), '.', ',');
            return s;
        };

        for (const auto& measurement : g_cbrMeasurements) {
            cbrOut << toComma(measurement.first) << ";" << toComma(measurement.second) << "\n";
        }
        cbrOut.close();

        double avgCbr = 0.0;
        for (const auto& m : g_cbrMeasurements) {
            avgCbr += m.second;
        }
        avgCbr /= g_cbrMeasurements.size();

        std::cout << "==== CBR Summary (Node " << g_cbrNodeId << ") ====\n";
        std::cout << "Measurements: " << g_cbrMeasurements.size() << "\n";
        std::cout << std::fixed << std::setprecision(4) << "Average CBR:  " << avgCbr << "\n";
        std::cout << "Wrote CBR data to: " << cbrCsvPath.string() << "\n";
        std::cout << "===============================\n\n";
    }

    {
        const std::string baseName = fs::path(g_outCsv).stem().string();
        const fs::path pltPath = fs::path(g_graphDir) / (baseName + ".plt");
        const fs::path pngPath = fs::path(g_graphDir) / (baseName + ".png");

        std::ofstream gp(pltPath.string().c_str());
        gp << "set terminal pngcairo size 1000,700 enhanced\n";
        gp << "set output '" << pngPath.string() << "'\n";
        gp << "set datafile separator ';'\n";
        gp << "set title 'PRR vs Distance'\n";
        gp << "plot '< tail -n +2 " << g_outCsv << " | sed s/,/./g' using 1:2 with linespoints title \"PRR\"\n";
        gp << "set xlabel 'Distance upper-edge (m)'\n";
        gp << "set ylabel 'Packet Reception Ratio'\n";
        gp << "set grid\n";
        gp << "set yrange [0:1]\n";
        gp.close();

        int rc = std::system(std::string("gnuplot \"" + pltPath.string() + "\"").c_str());
        if (rc != 0) {
            std::cout << "Note: Manual gnuplot required. Run: gnuplot " << pltPath.string() << std::endl;
        } else {
            std::cout << "Wrote PRR plot to: " << pngPath.string() << std::endl;
        }
    }

    return 0;
}

