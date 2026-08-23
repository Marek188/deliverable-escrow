/**
 * Deploys DeliverableEscrow. Run with the GenLayer CLI, which executes
 * this file against whichever network `genlayer network` currently
 * points at (localnet, studionet, or testnet_asimov):
 *
 *     genlayer deploy
 *
 * See https://docs.genlayer.com/developers/intelligent-contracts/deploying/deploy-scripts
 */
import { deployContract } from "genlayer-js";
import type { GenLayerClient } from "genlayer-js/types";

export default async function main(client: GenLayerClient<any>) {
  const escrow = await deployContract(client, "contracts/deliverable_escrow.py", []);

  console.log("DeliverableEscrow deployed at:", escrow.address);
  console.log(
    "Next: call create_engagement(provider, brief, deadline_hours) with some GEN value to open your first engagement.",
  );

  return { escrow };
}
