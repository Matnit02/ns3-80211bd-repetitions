/* -*-  Mode: C++; c-file-style: "gnu"; indent-tabs-mode:nil; -*- */
/*
 * This software was developed at the National Institute of Standards and
 * Technology by employees of the Federal Government in the course of
 * their official duties. Pursuant to titleElement 17 Section 105 of the United
 * States Code this software is not subject to copyright protection and
 * is in the public domain.
 * NIST assumes no responsibility whatsoever for its use by other parties,
 * and makes no guarantees, expressed or implied, about its quality,
 * reliability, or any other characteristic.
 * 
 * We would appreciate acknowledgement if the software is used.
 * 
 * NIST ALLOWS FREE USE OF THIS SOFTWARE IN ITS "AS IS" CONDITION AND
 * DISCLAIM ANY LIABILITY OF ANY KIND FOR ANY DAMAGES WHATSOEVER RESULTING
 * FROM THE USE OF THIS SOFTWARE.
 * 
 * Modified by: NIST
 * It was tested under ns-3.22
 */

#include "ns3/log.h"
#include "ns3/double.h"
#include "ns3/enum.h"
#include "ns3/boolean.h"
#include "ns3/mobility-model.h"
#include "ns3/random-variable-stream.h"
#include <cmath>
#include "nist-outdoor-propagation-loss-model.h"
#include <ns3/node.h>
#include <algorithm>
//#include <ns3/lte-enb-net-device.h>
//#include <ns3/lte-ue-net-device.h>

NS_LOG_COMPONENT_DEFINE ("NistOutdoorPropagationLossModel");

namespace ns3 {

NS_OBJECT_ENSURE_REGISTERED (NistOutdoorPropagationLossModel);


TypeId
NistOutdoorPropagationLossModel::GetTypeId (void)
{
  static TypeId tid = TypeId ("ns3::NistOutdoorPropagationLossModel")
    .SetParent<PropagationLossModel> ()
    .AddConstructor<NistOutdoorPropagationLossModel> ()
    .AddAttribute ("Frequency",
                    "The propagation frequency in Hz",
                    DoubleValue (763e6),
                    MakeDoubleAccessor (&NistOutdoorPropagationLossModel::m_frequency),
                    MakeDoubleChecker<double> ())
    .AddAttribute ("MatlabB1LosOnly",
                   "Enable MATLAB-like WINNER+ B1 LOS-only model (skip NLOS/walls).",
                   BooleanValue (false),
                   MakeBooleanAccessor (&NistOutdoorPropagationLossModel::m_matlabB1LosOnlyEnabled),
                   MakeBooleanChecker ())
    .AddAttribute ("MatlabB1AntennaHeightM",
                   "Common antenna height (m) used to compute h_eff = h - offset.",
                   DoubleValue (1.5),
                   MakeDoubleAccessor (&NistOutdoorPropagationLossModel::m_antennaHeightM),
                   MakeDoubleChecker<double> (0.1))
    .AddAttribute ("MatlabB1EffHeightOffsetM",
                   "Effective height offset (m) so that h_eff = h - offset.",
                   DoubleValue (1.0),
                   MakeDoubleAccessor (&NistOutdoorPropagationLossModel::m_effHeightOffsetM),
                   MakeDoubleChecker<double> (0.0))
    .AddAttribute ("MatlabB1SigmaLosDb",
                   "LOS shadowing sigma (dB) for MATLAB-style LOS-only model.",
                   DoubleValue (4.0),
                   MakeDoubleAccessor (&NistOutdoorPropagationLossModel::m_sigmaLosDb),
                   MakeDoubleChecker<double> (0.0))
    .AddAttribute ("MatlabB1DecorrelM",
                   "Shadowing decorrelation distance (m) for exponential kernel.",
                   DoubleValue (25.0),
                   MakeDoubleAccessor (&NistOutdoorPropagationLossModel::m_decorrelationM),
                   MakeDoubleChecker<double> (0.0))
    .AddAttribute ("MatlabB1CorrelatedShadowing",
                   "Enable correlated LOS shadowing (true) or uncorrelated (false).",
                   BooleanValue (true),
                   MakeBooleanAccessor (&NistOutdoorPropagationLossModel::m_correlatedShadowing),
                   MakeBooleanChecker ())
    ;

  return tid;
}

NistOutdoorPropagationLossModel::NistOutdoorPropagationLossModel ()
  : PropagationLossModel ()
{  
  m_rand  = CreateObject<UniformRandomVariable> ();
  // Defaults for MATLAB B1 LOS-only path
  m_matlabB1LosOnlyEnabled = false;
  m_antennaHeightM         = 1.5;
  m_effHeightOffsetM       = 1.0;
  m_sigmaLosDb             = 4.0;
  m_decorrelationM         = 25.0;
  m_correlatedShadowing    = true;
  m_norm  = CreateObject<NormalRandomVariable> (); // mean=0, var=1
}

NistOutdoorPropagationLossModel::~NistOutdoorPropagationLossModel ()
{
}

double
NistOutdoorPropagationLossModel::GetLoss (Ptr<MobilityModel> a, Ptr<MobilityModel> b) const
{
  // ===== MATLAB-like WINNER+ B1 LOS-only fast path =====
  if (m_matlabB1LosOnlyEnabled)
    {
      // Distance (m) and frequency (GHz)
      const double d    = a->GetDistanceFrom (b);
      if (d <= 0.0) { return 0.0; }
      const double fGHz = m_frequency / 1e9;
      const double c    = 299792458.0; // m/s

      // Effective height; MATLAB default h=1.5, offset=1.0 → h_eff=0.5
      const double hEff = std::max (0.1, m_antennaHeightM - m_effHeightOffsetM);

      // Breakpoint distance: Dbp = 4*h_eff^2 * f(Hz) / c
      const double Dbp = (4.0 * hEff * hEff * (fGHz * 1e9)) / c;

      // LOS piecewise coefficients (convert to linear via 10^(dB/10))
      const double L0mid_dB = 27.0 + 20.0 * std::log10 (fGHz);
      const double bmid     = 2.27;
      const double L0far_dB = 7.56 - 34.6 * std::log10 (hEff) + 2.7 * std::log10 (fGHz);
      const double bfar     = 4.0;
      auto db2lin = [] (double db) { return std::pow (10.0, db / 10.0); };
      const double L0mid = db2lin (L0mid_dB);
      const double L0far = db2lin (L0far_dB);

      // LOS-only path loss (linear), then to dB
      const double PL_lin = (d <= Dbp) ? (L0mid * std::pow (d, bmid))
                                       : (L0far * std::pow (d, bfar));
      double PL_dB = 10.0 * std::log10 (PL_lin);

      // Distance-correlated LOS shadowing (exponential kernel)
      double shadow_dB = 0.0;
      if (m_sigmaLosDb > 0.0)
        {
          // Use canonical (a,b) or (b,a) ordering to share state
          MobilityDuo key;
          key.a = a; key.b = b;
          if (key.b < key.a) { std::swap (key.a, key.b); }

          const double lastD = (m_lastPairDistM.count (key) ? m_lastPairDistM.at (key) : d);
          const double delta = std::fabs (d - lastD);
          const double Dc    = std::max (1e-6, m_decorrelationM);
          const double rho   = std::exp (- delta / Dc); // e^{-Δ/Dc}

          const double prev  = (m_shadowDb.count (key) ? m_shadowDb.at (key) : 0.0);
          double innov = 0.0;
          if (m_correlatedShadowing)
            {
              const double w  = std::sqrt (std::max (0.0, 1.0 - std::exp (-2.0 * delta / Dc)));
              innov = w * (m_norm->GetValue () * m_sigmaLosDb); // GetValue() ~ N(0,1)
            }
          else
            {
              innov = (m_norm->GetValue () * m_sigmaLosDb);
            }

          shadow_dB = rho * prev + innov;
          m_shadowDb[key]      = shadow_dB;
          m_lastPairDistM[key] = d;
        }

      const double loss_dB = std::max (0.0, PL_dB + shadow_dB); // cap: no gain
      return loss_dB;
    }

  // Free space pathloss
  double loss = 0.0;
  // Frequency in GHz
  double fc = m_frequency / 1e9;
  // Distance between the two nodes in meter
  double dist = a->GetDistanceFrom (b);

  // Calculate the pathloss based on 3GPP specifications : 3GPP TR 36.843 V12.0.1
  // WINNER II Channel Models, D1.1.2 V1.2., Equation (4.24) p.43, available at
  // http://www.cept.org/files/1050/documents/winner2%20-%20final%20report.pdf
  loss = 20 * std::log10 (dist) + 46.6 + 20 * std::log10 (fc / 5.0);
  NS_LOG_INFO (this << "Outdoor , the free space loss = " << loss);

  // WINNER II channel model for Urban Microcell scenario (UMi) : B1
  double pl_b1 = 0.0;
  // Actual antenna heights (1.5 m for UEs)
  double hms = a->GetPosition ().z;
  double hbs = b->GetPosition ().z;
  // Effective antenna heights (0.8 m for UEs)
  double hbs1 = hbs - 1;
  double hms1 = hms - 0.7;
  // Propagation velocity in free space
  double c = 3 * std::pow (10, 8);
  // LOS offset = LOS loss to add to the computed pathloss
  double los = 0;
  // NLOS offset = NLOS loss to add to the computed pathloss
  double nlos = -5;

  double d1 = 4 * hbs1 * hms1 * m_frequency * (1 / c);

  // Calculate the LOS probability based on 3GPP specifications : 3GPP TR 36.843 V12.0.1
  // WINNER II Channel Models, D1.1.2 V1.2., Table 4-7 p.48, available at
  // http://www.cept.org/files/1050/documents/winner2%20-%20final%20report.pdf
  double plos = std::min ((18 / dist), 1.0) * (1 - std::exp (-dist / 36)) + std::exp (-dist / 36);

  // Compute the WINNER II B1 pathloss based on 3GPP specifications : 3GPP TR 36.843 V12.0.1
  // D5.3: WINNER+ Final Channel Models, Table 4-1 p.74, available at
  // http://projects.celtic-initiative.org/winner%2B/WINNER+%20Deliverables/D5.3_v1.0.pdf

  // Generate a random number between 0 and 1 (if it doesn't already exist) to evaluate the LOS/NLOS situation
  double r = 0.0;

  MobilityDuo couple;
  couple.a = a;
  couple.b = b;
  std::map<MobilityDuo, double>::iterator it_a = m_randomMap.find (couple);
  if (it_a != m_randomMap.end ())
  {
    r = it_a->second;
  }
  else
  {
    couple.a = b;
    couple.b = a;
    std::map<MobilityDuo, double>::iterator it_b = m_randomMap.find (couple);
    if (it_b != m_randomMap.end ())
    {
      r = it_b->second;
    }
    else
    {
      m_randomMap[couple] = m_rand->GetValue (0,1);
      r = m_randomMap[couple];
    }
  }

  // This model is only valid to a minimum distance of 3 meters
  if (dist >= 3)
  {
    if (r <= plos)
    {
      // LOS
      if (dist <= d1)
      {
        pl_b1 = 22.7 * std::log10 (dist) + 27.0 + 20.0 * std::log10 (fc) + los;
        NS_LOG_INFO (this << "Outdoor LOS (Distance <= " << d1 << ") : the WINNER B1 loss = " << pl_b1);
      }
      else
      {
        pl_b1 = 40 * std::log10 (dist) + 7.56 - 17.3 * std::log10 (hbs1) - 17.3 * std::log10 (hms1) + 2.7 * std::log10 (fc) + los;
        NS_LOG_INFO (this << "Outdoor LOS (Distance > " << d1 << ") : the WINNER B1 loss = " << pl_b1);
      }
    }
    else
    {
      // NLOS
      if ((fc >= 0.758) and (fc <= 0.798))
      {
        // Frequency = 700 MHz for Public Safety
        pl_b1 = (44.9 - 6.55 * std::log10 (hbs)) * std::log10 (dist) + 5.83 * std::log10 (hbs) + 16.33 + 26.16 * std::log10 (fc) + nlos;
        NS_LOG_INFO (this << "Outdoor NLOS (Frequency 0.7 GHz) , the WINNER B1 loss = " << pl_b1);
      }
      if ((fc >= 1.92) and (fc <= 2.17))
      {
        // Frequency = 2 GHz for general scenarios
        pl_b1 = (44.9 - 6.55 * std::log10 (hbs)) * std::log10 (dist) + 5.83 * std::log10 (hbs) + 14.78 + 34.97 * std::log10 (fc) + nlos;
        NS_LOG_INFO (this << "Outdoor NLOS (Frequency 2 GHz) , the WINNER B1 loss = " << pl_b1);
      }
    }
  }

  loss = std::max (loss, pl_b1);
  return std::max (0.0, loss);
}

double 
NistOutdoorPropagationLossModel::DoCalcRxPower (double txPowerDbm,
					       Ptr<MobilityModel> a,
					       Ptr<MobilityModel> b) const
{
  return (txPowerDbm - GetLoss (a, b));
}

int64_t
NistOutdoorPropagationLossModel::DoAssignStreams (int64_t stream)
{
  // Ensure reproducibility: assign streams to both RNGs
  if (m_rand)
    {
      m_rand->SetStream (stream);
      ++stream;
    }
  if (m_norm)
    {
      m_norm->SetStream (stream);
      ++stream;
    }
  return stream;
}


} // namespace ns3
